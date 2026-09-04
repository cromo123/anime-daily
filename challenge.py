import random
import sqlite3
from datetime import date

from database import (
    DATABASE_PATH,
    load_anime_records,
    load_challenge_record,
    load_recent_anime_ids,
    load_recent_matchup_pairs,
    normalize_matchup_pair,
    record_challenge,
)


ANIME_PER_CATEGORY = 6
MAX_GENERATION_ATTEMPTS = 500
RECENT_ANIME_DAYS = 7
RECENT_MATCHUP_DAYS = 90

CATEGORY_RULES = [
    {
        "name": "Higher Score",
        "metric": "score",
        "metric_label": "Score",
        "question": "Which anime has the higher score?",
    },
    {
        "name": "More Popular",
        "metric": "members",
        "metric_label": "Members",
        "question": "Which anime is more popular?",
    },
    {
        "name": "More Episodes",
        "metric": "series_episodes",
        "metric_label": "Series episodes",
        "question": "Which anime series has more episodes?",
    },
    {
        "name": "More Recent",
        "metric": "release_date",
        "metric_label": "Release date",
        "question": "Which anime is more recent?",
    },
    {
        "name": "Longer Runtime",
        "metric": "runtime_minutes",
        "metric_label": "Runtime in minutes",
        "question": "Which movie has the longer runtime?",
    },
]


def get_comparison_value(anime, metric):
    value = anime.get(metric)

    if value is None:
        return None

    if metric == "release_date":
        try:
            return date.fromisoformat(value)
        except (TypeError, ValueError):
            return None

    return value


def is_eligible(anime, category, used_anime_ids, blocked_anime_ids):
    mal_id = anime.get("mal_id")

    if (
        mal_id is None
        or mal_id in used_anime_ids
        or mal_id in blocked_anime_ids
    ):
        return False

    if category["name"] == "Longer Runtime" and anime.get("type") != "movie":
        return False

    if category["name"] == "More Popular" and anime.get("popularity_rank") is None:
        return False

    return get_comparison_value(anime, category["metric"]) is not None


def select_category_anime(
    catalog,
    category,
    used_anime_ids,
    blocked_anime_ids,
    recent_matchup_pairs,
    random_source,
):
    candidates_by_id = {}

    for anime in catalog:
        if is_eligible(anime, category, used_anime_ids, blocked_anime_ids):
            candidates_by_id[anime["mal_id"]] = anime

    candidates_by_value = {}

    for anime in candidates_by_id.values():
        value = get_comparison_value(anime, category["metric"])
        candidates_by_value.setdefault(value, []).append(anime)

    for candidates in candidates_by_value.values():
        random_source.shuffle(candidates)

    selected_anime = []
    previous_value = None
    previous_anime = None

    while len(selected_anime) < ANIME_PER_CATEGORY:
        available_candidates_by_value = {}

        for value, candidates in candidates_by_value.items():
            if value == previous_value:
                continue

            available_candidates = []

            for anime in candidates:
                if previous_anime is not None:
                    matchup_pair = normalize_matchup_pair(
                        previous_anime["mal_id"],
                        anime["mal_id"],
                    )

                    if matchup_pair in recent_matchup_pairs:
                        continue

                available_candidates.append(anime)

            if available_candidates:
                available_candidates_by_value[value] = available_candidates

        if not available_candidates_by_value:
            return None

        largest_group_size = max(
            len(candidates)
            for candidates in available_candidates_by_value.values()
        )
        largest_values = [
            value
            for value, candidates in available_candidates_by_value.items()
            if len(candidates) == largest_group_size
        ]
        selected_value = random_source.choice(largest_values)
        chosen_anime = random_source.choice(
            available_candidates_by_value[selected_value]
        )
        candidates_by_value[selected_value].remove(chosen_anime)
        selected_anime.append(chosen_anime)
        previous_value = selected_value
        previous_anime = chosen_anime

    return selected_anime


def try_generate_challenge(
    catalog,
    blocked_anime_ids,
    recent_matchup_pairs,
    random_source,
):
    runtime_category = CATEGORY_RULES[-1]
    selection_order = [runtime_category] + CATEGORY_RULES[:-1]

    for _ in range(MAX_GENERATION_ATTEMPTS):
        used_anime_ids = set()
        selected_by_category = {}

        for category in selection_order:
            selected_anime = select_category_anime(
                catalog,
                category,
                used_anime_ids,
                blocked_anime_ids,
                recent_matchup_pairs,
                random_source,
            )

            if selected_anime is None:
                break

            selected_by_category[category["name"]] = selected_anime
            used_anime_ids.update(anime["mal_id"] for anime in selected_anime)

        if len(selected_by_category) == len(CATEGORY_RULES):
            challenge = []

            for category_rule in CATEGORY_RULES:
                category = category_rule.copy()
                category["anime"] = selected_by_category[category_rule["name"]]
                challenge.append(category)

            return challenge

    return None


def generate_challenge(
    catalog,
    random_source=None,
    recent_anime_ids=None,
    recent_matchup_pairs=None,
):
    if random_source is None:
        random_source = random

    if recent_anime_ids is None:
        recent_anime_ids = set()

    if recent_matchup_pairs is None:
        recent_matchup_pairs = set()

    unique_anime_ids = {
        anime.get("mal_id") for anime in catalog if anime.get("mal_id") is not None
    }

    if len(unique_anime_ids) < ANIME_PER_CATEGORY * len(CATEGORY_RULES):
        raise RuntimeError(
            "The catalog needs at least 30 unique MAL entries to build a complete "
            "challenge."
        )

    challenge = try_generate_challenge(
        catalog,
        recent_anime_ids,
        recent_matchup_pairs,
        random_source,
    )

    if challenge is not None:
        return challenge

    challenge = try_generate_challenge(
        catalog,
        set(),
        recent_matchup_pairs,
        random_source,
    )

    if challenge is not None:
        return challenge

    raise RuntimeError(
        "The catalog could not produce a complete valid challenge, even after "
        "relaxing recent-anime avoidance. Factual validity, movie-only runtime, "
        "unique MAL IDs, and recent exact-matchup prevention remain required."
    )


def challenge_from_record(challenge_record):
    placements_by_category = {}

    for placement in challenge_record["placements"]:
        category_name = placement["category"]
        position = placement["position"]
        anime = placement.copy()
        del anime["category"]
        del anime["position"]
        placements_by_category.setdefault(category_name, []).append((position, anime))

    challenge = []

    for category_rule in CATEGORY_RULES:
        placements = placements_by_category.get(category_rule["name"], [])
        placements.sort(key=lambda placement: placement[0])

        if len(placements) != ANIME_PER_CATEGORY:
            raise RuntimeError("Stored challenge data is incomplete.")

        category = category_rule.copy()
        category["anime"] = [anime for _, anime in placements]
        challenge.append(category)

    return challenge


def load_stored_challenge(challenge_date, database_path=DATABASE_PATH):
    challenge_record = load_challenge_record(challenge_date, database_path)

    if challenge_record is None:
        return None

    return challenge_from_record(challenge_record)


def generate_history_aware_challenge(
    challenge_date,
    database_path=DATABASE_PATH,
    random_source=None,
):
    catalog = load_anime_records(database_path)
    recent_anime_ids = load_recent_anime_ids(
        challenge_date,
        RECENT_ANIME_DAYS,
        database_path,
    )
    recent_matchup_pairs = load_recent_matchup_pairs(
        challenge_date,
        RECENT_MATCHUP_DAYS,
        database_path,
    )

    return generate_challenge(
        catalog,
        random_source,
        recent_anime_ids,
        recent_matchup_pairs,
    )


def get_or_create_daily_challenge(
    challenge_date,
    database_path=DATABASE_PATH,
    random_source=None,
):
    stored_challenge = load_stored_challenge(challenge_date, database_path)

    if stored_challenge is not None:
        return stored_challenge

    challenge = generate_history_aware_challenge(
        challenge_date,
        database_path,
        random_source,
    )

    try:
        record_challenge(challenge, challenge_date, database_path)
    except sqlite3.IntegrityError:
        stored_challenge = load_stored_challenge(challenge_date, database_path)

        if stored_challenge is not None:
            return stored_challenge

        raise

    return challenge


def serialize_public_challenge(challenge_date, challenge):
    categories = []

    for category in challenge:
        public_anime = [
            {
                "position": position,
                "mal_id": anime["mal_id"],
                "title": anime["title"],
                "type": anime["type"],
            }
            for position, anime in enumerate(category["anime"], start=1)
        ]
        categories.append(
            {
                "name": category["name"],
                "question": category["question"],
                "anime": public_anime,
            }
        )

    return {
        "challenge_date": str(challenge_date),
        "categories": categories,
    }


def evaluate_category_comparison(category, comparison_position, selected_mal_id):
    if comparison_position < 1 or comparison_position >= len(category["anime"]):
        raise ValueError("comparison_position must be between 1 and 5.")

    anime_a = category["anime"][comparison_position - 1]
    anime_b = category["anime"][comparison_position]

    if selected_mal_id not in [anime_a["mal_id"], anime_b["mal_id"]]:
        raise ValueError("selected_mal_id must identify one anime in the comparison.")

    metric = category["metric"]
    value_a = get_comparison_value(anime_a, metric)
    value_b = get_comparison_value(anime_b, metric)

    if value_a is None or value_b is None or value_a == value_b:
        raise RuntimeError("The stored challenge contains an invalid matchup.")

    if value_a > value_b:
        correct_mal_id = anime_a["mal_id"]
    else:
        correct_mal_id = anime_b["mal_id"]

    revealed_anime = []

    for anime in [anime_a, anime_b]:
        revealed = {
            "mal_id": anime["mal_id"],
            "title": anime["title"],
        }

        if category["name"] == "More Popular":
            revealed["popularity_rank"] = anime["popularity_rank"]
            revealed["members"] = anime["members"]
        else:
            revealed[metric] = anime[metric]

        revealed_anime.append(revealed)

    return {
        "category": category["name"],
        "comparison_position": comparison_position,
        "selected_mal_id": selected_mal_id,
        "correct": selected_mal_id == correct_mal_id,
        "correct_mal_id": correct_mal_id,
        "revealed_anime": revealed_anime,
    }


def evaluate_comparison(
    challenge,
    category_name,
    comparison_position,
    selected_mal_id,
):
    for category in challenge:
        if category["name"] == category_name:
            return evaluate_category_comparison(
                category,
                comparison_position,
                selected_mal_id,
            )

    raise ValueError(f"Unknown category: {category_name}")
