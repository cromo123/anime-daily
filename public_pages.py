"""Crawlable public history, using stored facts and the existing comparisons."""

from datetime import datetime, timedelta, timezone
from html import escape
import math

from challenge import (LegacyChallengeError, challenge_from_record,
                       contains_known_synthetic_fixture, evaluate_category_comparison,
                       get_comparison_value, has_eligible_display_type)
from database import (load_public_archive_records, load_challenge_record,
                      load_comparison_answer_stats, load_series_representatives)


SITE_URL = "https://animoredle.com"
PAGE_SIZE = 20
GUIDE_PATHS = {
    "Higher Score": "/guides/higher-score",
    "More Popular": "/guides/more-popular",
    "More Episodes": "/guides/more-episodes",
    "More Recent": "/guides/more-recent",
}
STATIC_PAGES = {
    "/": "index.html", "/about": "about.html", "/privacy": "privacy.html",
    "/contact": "contact.html", "/how-to-play": "how-to-play.html",
    "/methodology": "methodology.html", "/faq": "faq.html",
    "/feedback": "feedback.html", "/guides/higher-score": "higher-score.html",
    "/guides/more-popular": "more-popular.html",
    "/guides/more-episodes": "more-episodes.html",
    "/guides/more-recent": "more-recent.html",
}
FOOTER = """<footer class="site-footer"><nav aria-label="Site information">
<a href="/how-to-play">How to Play</a><a href="/methodology">Methodology</a>
<a href="/past-challenges">Past Challenges</a><a href="/faq">FAQ</a>
<a href="/feedback">Feedback</a><a href="/about">About</a>
<a href="/privacy">Privacy</a><a href="/contact">Contact</a>
</nav></footer>"""


def historical_cutoff(now=None):
    """Exclude a day until it has ended even in UTC-12; clients cannot override it."""
    now = datetime.now(timezone.utc) if now is None else now
    return (now - timedelta(hours=12)).date()


def mal_link(mal_id, title):
    if type(mal_id) is not int or mal_id <= 0:
        return escape(str(title))
    return (f'<a class="mal-link" href="https://myanimelist.net/anime/{mal_id}" '
            f'target="_blank" rel="noopener noreferrer">{escape(str(title))}'
            '<span aria-hidden="true"> ↗</span><span class="visually-hidden"> '
            '(MyAnimeList, opens in a new tab)</span></a>')


def document(title, description, path, body, noindex=False):
    robots = '<meta name="robots" content="noindex">' if noindex else ""
    return f'''<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="google-adsense-account" content="ca-pub-7045982233365027">
<title>{escape(title)} | AniMoredle</title><meta name="description" content="{escape(description, quote=True)}">
<link rel="canonical" href="{SITE_URL}{escape(path, quote=True)}">{robots}
<link rel="stylesheet" href="/static/styles.css?v=public-site-1"></head><body class="information-page">
<div class="page-glow" aria-hidden="true"></div><main class="information-shell public-history-shell">
<header class="information-header"><a class="brand" href="/" aria-label="AniMoredle home">
<span class="brand-mark" aria-hidden="true">AN</span><span>AniMoredle</span></a>
<a class="information-home-link" href="/">Play today’s challenge</a></header>
<article class="information-card">{body}</article></main>{FOOTER}</body></html>'''


def readable_date(value):
    return value.strftime("%B %d, %Y").replace(" 0", " ")


def valid_historical_challenge(record):
    """Historical facts need shape/metric checks, not today's cooldown recalculation."""
    try:
        challenge = challenge_from_record(record)
        if contains_known_synthetic_fixture(challenge):
            return None
        ids = [anime["mal_id"] for category in challenge for anime in category["anime"]]
        if len(ids) != 24 or len(set(ids)) != 24:
            return None
        for category in challenge:
            for anime in category["anime"]:
                value = get_comparison_value(anime, category["metric"])
                if (type(anime["mal_id"]) is not int or anime["mal_id"] <= 0
                        or not has_eligible_display_type(anime, category)
                        or value is None
                        or isinstance(value, (float, int)) and not math.isfinite(value)):
                    return None
                if category["name"] == "More Popular" and anime.get("popularity_rank") is None:
                    return None
            for index in range(1, 6):
                evaluate_category_comparison(category, index, category["anime"][index - 1]["mal_id"])
        return challenge
    except (LegacyChallengeError, KeyError, TypeError, ValueError, RuntimeError):
        return None


def archive_index(database_path, minimum_date, page=1):
    records = load_public_archive_records(historical_cutoff(), minimum_date,
                                          database_path, PAGE_SIZE + 1, (page - 1) * PAGE_SIZE)
    items = []
    for record, _ in available_historical_challenges(records[:PAGE_SIZE], database_path):
        day = datetime.strptime(record["challenge_date"], "%Y-%m-%d").date()
        iso = day.isoformat()
        items.append(f'<li><h2><a href="/past-challenges/{iso}">{readable_date(day)}</a></h2>'
                     '<p>Four rounds · 20 comparisons. Read the lineup, answers, and available community statistics.</p>'
                     f'<a href="/?date={iso}">Play this day</a></li>')
    content = '<ul class="past-challenge-list">' + "".join(items) + '</ul>' if items else (
        '<p>No published recaps are available on this page yet. '
        'Try <a href="/">today’s challenge</a> or read <a href="/how-to-play">How to Play</a>.</p>')
    pagination = '<nav class="pagination" aria-label="Archive pages">'
    if page > 1:
        pagination += f'<a href="/past-challenges?page={page - 1}">Newer challenges</a>'
    if len(records) > PAGE_SIZE:
        pagination += f'<a href="/past-challenges?page={page + 1}">Older challenges</a>'
    pagination += '</nav>'
    body = ('<p class="eyebrow">The daily game, revisited</p><h1>Past challenges</h1>'
            '<p>Explore published anime lineups and the facts behind each comparison, or play a day you missed. '
            'Recaps contain spoilers. They appear only after the day has ended worldwide, so today’s answers stay hidden.</p>'
            '<p>Your saved scores and the playable calendar are available from the <a href="/">game’s Archive tab</a>.</p>'
            + content + pagination)
    path = '/past-challenges' + (f'?page={page}' if page > 1 else '')
    return document("Past daily anime challenges" + (f" — page {page}" if page > 1 else ""),
                    "Browse AniMoredle’s published daily anime challenges, factual comparison recaps, and playable past days.", path, body)


def available_historical_challenges(records, database_path):
    """Validate a bounded page and resolve its series metadata in one batch."""
    checked = [(record, valid_historical_challenge(record)) for record in records]
    ids = [anime["mal_id"] for _, challenge in checked if challenge
           for category in challenge if category["name"] == "More Episodes"
           for anime in category["anime"]]
    representatives = load_series_representatives(ids, database_path) if ids else {}
    return [(record, challenge) for record, challenge in checked if challenge
            and all(representatives.get(anime["mal_id"]) is not None
                    for category in challenge if category["name"] == "More Episodes"
                    for anime in category["anime"])]


def metric_text(category, anime):
    name = category["name"]
    if name == "Higher Score":
        return f'Score: {anime["score"]:g}'
    if name == "More Popular":
        return f'Popularity rank #{anime["popularity_rank"]:,} · {anime["members"]:,} members'
    if name == "More Episodes":
        return f'{anime["series_episodes"]:,} series episodes'
    return f'Release date: {anime["release_date"]}'


def archive_detail(day, database_path, minimum_date):
    if day < minimum_date or day >= historical_cutoff():
        return None
    record = load_challenge_record(day.isoformat(), database_path)
    challenge = valid_historical_challenge(record) if record else None
    if challenge is None:
        return None
    series_ids = [a["mal_id"] for c in challenge if c["name"] == "More Episodes" for a in c["anime"]]
    representatives = load_series_representatives(series_ids, database_path)
    if any(value is None for value in representatives.values()):
        return None
    stats = {(s["category"], s["comparison_position"]): s
             for s in load_comparison_answer_stats(record["id"], database_path)}
    body = (f'<p class="eyebrow">Historical recap · spoilers</p><h1>{readable_date(day)}</h1>'
            '<p>See the five chained comparisons in each round. These values reflect the stored catalog available '
            'when this recap is read; refreshed scores, membership counts, or series metadata can change over time.</p>'
            '<p>Comparison values and correct choices below use the current catalog, not a frozen snapshot of that day. '
            'Community percentages use correctness recorded when each official answer was first submitted. '
            'They are not re-scored after catalog updates, so a historical percentage may not match the current correct choice.</p>'
            f'<p><a class="text-cta" href="/?date={day.isoformat()}">Play this challenge</a> '
            'or <a href="/past-challenges">browse other days</a>.</p>')
    for category in challenge:
        body += f'<section class="recap-category"><h2>{escape(category["name"])}</h2>'
        body += f'<p><a href="{GUIDE_PATHS[category["name"]]}">How this category works</a></p>'
        if category["name"] == "More Episodes":
            body += '<p>Full anime series: the first-released representative identifies the connected series, not a single entry’s episode count.</p>'
        body += '<ol class="recap-comparisons">'
        for position in range(1, 6):
            pair = category["anime"][position - 1:position + 1]
            result = evaluate_category_comparison(category, position, pair[0]["mal_id"])
            body += f'<li><h3>Comparison {position}</h3>'
            winner = ""
            for anime in pair:
                representative = representatives.get(anime["mal_id"], anime)
                reference = mal_link(representative["mal_id"], representative["title"])
                body += f'<p>{reference}<br><span class="recap-metric">{escape(metric_text(category, anime))}</span></p>'
                if anime["mal_id"] == result["correct_mal_id"]:
                    winner = representative["title"]
            body += f'<p class="recap-winner">Correct choice: {escape(winner)}</p>'
            stat = stats.get((category["name"], position))
            if stat and stat["total_answers"] >= 3:
                body += f'<p class="review-stats">{stat["percentage"]:g}% recorded correct in original official answers ({stat["total_answers"]} answers).</p>'
            else:
                body += '<p class="review-stats">Not enough official answers for a useful community percentage yet.</p>'
            body += '</li>'
        body += '</ol></section>'
    body += '<p>Something looks wrong? <a href="/feedback">Report a data issue</a>. AniMoredle is independent of MyAnimeList.</p>'
    return document(f"{readable_date(day)} anime challenge recap",
                    f"Explore the anime and verified comparisons in AniMoredle’s {readable_date(day)} challenge, across four categories.",
                    f"/past-challenges/{day.isoformat()}", body)
