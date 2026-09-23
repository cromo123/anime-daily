import math

from challenge import generate_challenge_candidates
from curator_context import popularity_tier
from database import DATABASE_PATH


WILDCARD_DISTANCE_WEIGHT = 5.0
UNKNOWN_POPULARITY_PENALTY = 5.0
DEFAULT_CANDIDATE_POOL_SIZE = 20
DEFAULT_SHORTLIST_LIMIT = 8


def validate_rating_target(name, value):
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or not 0 <= value <= 100
    ):
        raise ValueError(f"{name} must be a finite number between 0 and 100.")


def count_candidate_outliers(candidate):
    wildcard_count = 0
    unknown_popularity_count = 0

    for category in candidate["categories"]:
        for anime in category["anime"]:
            tier = popularity_tier(anime.get("popularity_rank"))

            if tier == "wildcard":
                wildcard_count += 1
            elif tier == "unknown":
                unknown_popularity_count += 1

    return wildcard_count, unknown_popularity_count


def calculate_candidate_guidance(
    candidate,
    target_popularity,
    target_difficulty,
    target_modernity,
    wildcard_target,
):
    validate_rating_target("target_popularity", target_popularity)
    validate_rating_target("target_difficulty", target_difficulty)
    validate_rating_target("target_modernity", target_modernity)

    if (
        not isinstance(wildcard_target, int)
        or isinstance(wildcard_target, bool)
        or wildcard_target < 0
    ):
        raise ValueError("wildcard_target must be a non-negative integer.")

    ratings = candidate["ratings"]
    popularity_distance = abs(
        ratings["popularity_score"] - target_popularity
    )
    difficulty_distance = abs(
        ratings["difficulty_score"] - target_difficulty
    )
    modernity_distance = abs(
        ratings["modernity_score"] - target_modernity
    )
    average_metric_distance = (
        popularity_distance + difficulty_distance + modernity_distance
    ) / 3
    wildcard_count, unknown_popularity_count = count_candidate_outliers(
        candidate
    )
    wildcard_distance = abs(wildcard_count - wildcard_target)

    # Unknown ranks are tracked separately and penalized rather than silently
    # treating them as recognizable entries or requested wildcards.
    guidance_score = (
        average_metric_distance
        + wildcard_distance * WILDCARD_DISTANCE_WEIGHT
        + unknown_popularity_count * UNKNOWN_POPULARITY_PENALTY
    )

    return {
        "popularity_distance": round(popularity_distance, 2),
        "difficulty_distance": round(difficulty_distance, 2),
        "modernity_distance": round(modernity_distance, 2),
        "average_metric_distance": round(average_metric_distance, 2),
        "wildcard_count": wildcard_count,
        "wildcard_distance": wildcard_distance,
        "unknown_popularity_count": unknown_popularity_count,
        "unknown_popularity_penalty": (
            unknown_popularity_count * UNKNOWN_POPULARITY_PENALTY
        ),
        "guidance_score": round(guidance_score, 2),
    }


def shortlist_candidates(
    candidates,
    target_popularity,
    target_difficulty,
    target_modernity,
    wildcard_target,
    limit=DEFAULT_SHORTLIST_LIMIT,
):
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit must be a positive integer.")

    ranked_candidates = []

    for candidate in candidates:
        guidance = calculate_candidate_guidance(
            candidate,
            target_popularity,
            target_difficulty,
            target_modernity,
            wildcard_target,
        )
        ranked_candidates.append({**candidate, "guidance": guidance})

    ranked_candidates.sort(
        key=lambda candidate: (
            candidate["guidance"]["guidance_score"],
            candidate["guidance"]["average_metric_distance"],
            candidate["candidate_id"],
        )
    )
    return ranked_candidates[:limit]


def generate_brief_guided_shortlist(
    challenge_date,
    target_popularity,
    target_difficulty,
    target_modernity,
    wildcard_target,
    pool_size=DEFAULT_CANDIDATE_POOL_SIZE,
    limit=DEFAULT_SHORTLIST_LIMIT,
    database_path=DATABASE_PATH,
    random_source=None,
    include_pool=False,
):
    if (
        not isinstance(pool_size, int)
        or isinstance(pool_size, bool)
        or pool_size < 1
    ):
        raise ValueError("pool_size must be a positive integer.")

    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit must be a positive integer.")

    if limit > pool_size:
        raise ValueError("limit cannot be larger than pool_size.")

    candidates = generate_challenge_candidates(
        challenge_date,
        count=pool_size,
        database_path=database_path,
        random_source=random_source,
    )
    shortlist = shortlist_candidates(
        candidates,
        target_popularity,
        target_difficulty,
        target_modernity,
        wildcard_target,
        limit,
    )

    if include_pool:
        return {
            "candidate_pool": candidates,
            "shortlist": shortlist,
        }

    return shortlist
