from challenge import (
    POPULAR_RANK_PRIMARY_MAX,
    POPULAR_RANK_SECONDARY_MAX,
    load_stored_challenge,
)
from challenge_ratings import build_rating_context, rate_challenge
from database import (
    DATABASE_PATH,
    load_anime_records,
    load_recent_challenge_dates,
)


def count_popularity_tiers(challenge):
    counts = {
        "top_1000_count": 0,
        "secondary_count": 0,
        "wildcard_count": 0,
        "unknown_popularity_count": 0,
    }

    for category in challenge:
        for anime in category["anime"]:
            popularity_rank = anime.get("popularity_rank")

            if popularity_rank is None:
                counts["unknown_popularity_count"] += 1
            elif popularity_rank <= POPULAR_RANK_PRIMARY_MAX:
                counts["top_1000_count"] += 1
            elif popularity_rank <= POPULAR_RANK_SECONDARY_MAX:
                counts["secondary_count"] += 1
            else:
                counts["wildcard_count"] += 1

    return counts


def load_recent_challenge_history(
    target_challenge_date,
    database_path=DATABASE_PATH,
    count=7,
):
    challenge_dates = load_recent_challenge_dates(
        target_challenge_date,
        database_path,
        count,
    )

    if not challenge_dates:
        return []

    catalog = load_anime_records(database_path)
    rating_context = build_rating_context(catalog)
    history = []

    for challenge_date in challenge_dates:
        challenge = load_stored_challenge(challenge_date, database_path)

        if challenge is None:
            continue

        ratings = rate_challenge(
            challenge,
            rating_context=rating_context,
        )
        history.append(
            {
                "challenge_date": challenge_date,
                "popularity_score": ratings["popularity_score"],
                "difficulty_score": ratings["difficulty_score"],
                "modernity_score": ratings["modernity_score"],
                **count_popularity_tiers(challenge),
            }
        )

    return history


def format_score(score):
    return f"{score:.1f}"


def format_recent_challenge_history(history):
    sections = []

    for challenge in history:
        composition = (
            f"{challenge['top_1000_count']} top-1000 | "
            f"{challenge['secondary_count']} secondary | "
            f"{challenge['wildcard_count']} wildcard"
        )

        if challenge["unknown_popularity_count"]:
            composition += (
                f" | {challenge['unknown_popularity_count']} unknown"
            )

        sections.append(
            "\n".join(
                [
                    challenge["challenge_date"],
                    f"Popularity: {format_score(challenge['popularity_score'])}",
                    f"Difficulty: {format_score(challenge['difficulty_score'])}",
                    f"Modernity: {format_score(challenge['modernity_score'])}",
                    f"Composition: {composition}",
                ]
            )
        )

    return "\n\n---\n\n".join(sections)
