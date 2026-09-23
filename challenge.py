import random
import sqlite3
from datetime import date

from database import (
    DATABASE_PATH,
    load_anime_records,
    load_challenge_record,
    load_recent_category_anime_ids,
    load_recent_matchup_pairs,
    load_series_representatives,
    normalize_matchup_pair,
    record_challenge,
)
from fixture_safety import (
    KNOWN_SYNTHETIC_MAL_IDS,
    SYNTHETIC_TITLE_PATTERN,
    is_known_synthetic_fixture,
)


ANIME_PER_CATEGORY = 6
MAX_GENERATION_ATTEMPTS = 500
MAX_BATCH_ATTEMPTS_PER_CANDIDATE = 20
RECENT_ANIME_DAYS = 7
RECENT_MATCHUP_DAYS = 90
DISPLAY_MEDIA_TYPES = {"tv", "movie", "ona", "ova", "special", "tv_special"}
EPISODIC_MEDIA_TYPES = DISPLAY_MEDIA_TYPES - {"movie"}
POPULAR_RANK_PRIMARY_MAX = 1000
POPULAR_RANK_SECONDARY_MAX = 3000
# Repeating 5 makes a five-primary round four times as likely as a four-primary round.
POPULARITY_PRIMARY_SLOT_OPTIONS = (4, 5, 5, 5, 5)
POPULARITY_SECONDARY_SLOT_WEIGHT = 4
POPULARITY_WILDCARD_SLOT_WEIGHT = 1
POPULARITY_PRIMARY = "primary"
POPULARITY_SECONDARY = "secondary"
POPULARITY_WILDCARD = "wildcard"

OPENING_ORDER_TRIALS = 24
# Difficulty is a 0-100 percentile-based score; 45 is clear, not trivial.
OPENING_TARGET_DIFFICULTY = 45
OPENING_DIFFICULTY_WEIGHT = 1.5
OPENING_RELATIVE_DIFFICULTY_WEIGHT = 1.5
# Secondary entries are acceptable; wildcard/unknown openings need a much
# stronger difficulty benefit to be considered.
OPENING_TIER_PENALTIES = {
    POPULARITY_PRIMARY: 0,
    POPULARITY_SECONDARY: 18,
    POPULARITY_WILDCARD: 110,
    "unknown": 130,
}
OPENING_NEAR_BEST_MARGIN = 4

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
]
TOTAL_QUESTIONS = len(CATEGORY_RULES) * (ANIME_PER_CATEGORY - 1)


class LegacyChallengeError(ValueError):
    """A stored challenge uses an older category format and cannot be played."""


class PublicChallengeValidationError(ValueError):
    """A challenge is not safe to publish through the public API."""


def contains_known_synthetic_fixture(challenge):
    return any(
        is_known_synthetic_fixture(anime.get("mal_id"), anime.get("title"))
        for category in challenge
        for anime in category.get("anime", [])
    )


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


def has_eligible_display_type(anime, category):
    media_type = anime.get("type")

    if category["name"] == "More Episodes":
        return media_type in EPISODIC_MEDIA_TYPES

    return media_type in DISPLAY_MEDIA_TYPES


def get_popularity_tier(anime):
    popularity_rank = anime.get("popularity_rank")

    if (
        popularity_rank is not None
        and popularity_rank <= POPULAR_RANK_PRIMARY_MAX
    ):
        return POPULARITY_PRIMARY

    if (
        popularity_rank is not None
        and popularity_rank <= POPULAR_RANK_SECONDARY_MAX
    ):
        return POPULARITY_SECONDARY

    return POPULARITY_WILDCARD


def build_popularity_plan(random_source):
    primary_slots = random_source.choice(POPULARITY_PRIMARY_SLOT_OPTIONS)
    outlier_slots = ANIME_PER_CATEGORY - primary_slots
    outlier_choices = (
        [POPULARITY_SECONDARY] * POPULARITY_SECONDARY_SLOT_WEIGHT
        + [POPULARITY_WILDCARD] * POPULARITY_WILDCARD_SLOT_WEIGHT
    )
    plan = [POPULARITY_PRIMARY] * primary_slots

    for _ in range(outlier_slots):
        plan.append(random_source.choice(outlier_choices))

    random_source.shuffle(plan)
    return plan


def popularity_fallback_order(preferred_tier):
    if preferred_tier == POPULARITY_PRIMARY:
        return [POPULARITY_PRIMARY, POPULARITY_SECONDARY, POPULARITY_WILDCARD]

    if preferred_tier == POPULARITY_SECONDARY:
        return [POPULARITY_SECONDARY, POPULARITY_PRIMARY, POPULARITY_WILDCARD]

    return [POPULARITY_WILDCARD, POPULARITY_SECONDARY, POPULARITY_PRIMARY]


def is_eligible(
    anime,
    category,
    used_anime_ids,
    blocked_anime_ids,
    series_representative_ids=None,
):
    mal_id = anime.get("mal_id")

    if (
        mal_id is None
        or mal_id in used_anime_ids
        or mal_id in blocked_anime_ids
    ):
        return False

    if not has_eligible_display_type(anime, category):
        return False

    if (
        category["name"] == "More Episodes"
        and series_representative_ids is not None
        and mal_id not in series_representative_ids
    ):
        return False

    if category["name"] == "More Popular" and anime.get("popularity_rank") is None:
        return False

    return get_comparison_value(anime, category["metric"]) is not None


def choose_stratified_candidate(
    candidates,
    preferred_tier,
    random_source,
):
    for popularity_tier in popularity_fallback_order(preferred_tier):
        tier_candidates = [
            anime
            for anime in candidates
            if get_popularity_tier(anime) == popularity_tier
        ]

        if tier_candidates:
            return random_source.choice(tier_candidates)

    return None


def opening_popularity_tier(
    anime,
    category_name,
    series_representative_popularity_ranks,
):
    if (
        category_name == "More Episodes"
        and series_representative_popularity_ranks is not None
    ):
        rank = series_representative_popularity_ranks.get(anime["mal_id"])
    else:
        rank = anime.get("popularity_rank")

    if rank is None:
        return "unknown"
    if rank <= POPULAR_RANK_PRIMARY_MAX:
        return POPULARITY_PRIMARY
    if rank <= POPULAR_RANK_SECONDARY_MAX:
        return POPULARITY_SECONDARY
    return POPULARITY_WILDCARD


def has_valid_adjacent_matchups(anime_order, metric, recent_matchup_pairs):
    for anime_a, anime_b in zip(anime_order, anime_order[1:]):
        value_a = get_comparison_value(anime_a, metric)
        value_b = get_comparison_value(anime_b, metric)
        if value_a is None or value_b is None or value_a == value_b:
            return False
        pair = normalize_matchup_pair(anime_a["mal_id"], anime_b["mal_id"])
        if pair in recent_matchup_pairs:
            return False
    return True


def order_category_for_opening(
    selected_anime,
    category,
    recent_matchup_pairs,
    random_source,
    rating_context,
    series_representative_popularity_ranks=None,
):
    """Prefer a recognizable, moderately clear first comparison."""
    # Imported here because challenge_ratings imports the category rules above.
    from challenge_ratings import comparison_difficulty

    valid_orders = []
    for attempt in range(OPENING_ORDER_TRIALS + 1):
        anime_order = selected_anime.copy()
        if attempt:
            random_source.shuffle(anime_order)
        if not has_valid_adjacent_matchups(
            anime_order, category["metric"], recent_matchup_pairs
        ):
            continue

        opening_tier_penalty = sum(
            OPENING_TIER_PENALTIES[
                opening_popularity_tier(
                    anime,
                    category["name"],
                    series_representative_popularity_ranks,
                )
            ]
            for anime in anime_order[:2]
        )
        difficulty = comparison_difficulty(
            anime_order[0], anime_order[1], category["name"], rating_context
        )
        later_difficulties = [
            comparison_difficulty(
                anime_order[index],
                anime_order[index + 1],
                category["name"],
                rating_context,
            )
            for index in range(1, ANIME_PER_CATEGORY - 1)
        ]
        later_average = sum(later_difficulties) / len(later_difficulties)
        # Discourage a harder-than-rest opener without imposing a cutoff.
        opening_score = (
            opening_tier_penalty
            + abs(difficulty - OPENING_TARGET_DIFFICULTY)
            * OPENING_DIFFICULTY_WEIGHT
            + max(0, difficulty - later_average)
            * OPENING_RELATIVE_DIFFICULTY_WEIGHT
        )
        valid_orders.append((opening_score, anime_order))

    # The originally selected chain is always valid, so this cannot make
    # generation fail when a history-constrained reorder is impossible.
    best_score = min(score for score, _ in valid_orders)
    near_best = [
        anime_order
        for score, anime_order in valid_orders
        if score <= best_score + OPENING_NEAR_BEST_MARGIN
    ]
    return random_source.choice(near_best)


def select_category_anime(
    catalog,
    category,
    used_anime_ids,
    blocked_anime_ids,
    recent_matchup_pairs,
    random_source,
    series_representative_ids=None,
    rating_context=None,
    series_representative_popularity_ranks=None,
):
    candidates_by_id = {}

    for anime in catalog:
        if is_eligible(
            anime,
            category,
            used_anime_ids,
            blocked_anime_ids,
            series_representative_ids,
        ):
            candidates_by_id[anime["mal_id"]] = anime

    available_candidates = list(candidates_by_id.values())

    selected_anime = []
    previous_value = None
    previous_anime = None
    popularity_plan = build_popularity_plan(random_source)

    while len(selected_anime) < ANIME_PER_CATEGORY:
        valid_next_candidates = []

        for anime in available_candidates:
            value = get_comparison_value(anime, category["metric"])

            if value == previous_value:
                continue

            if previous_anime is not None:
                matchup_pair = normalize_matchup_pair(
                    previous_anime["mal_id"],
                    anime["mal_id"],
                )

                if matchup_pair in recent_matchup_pairs:
                    continue

            valid_next_candidates.append(anime)

        chosen_anime = choose_stratified_candidate(
            valid_next_candidates,
            popularity_plan[len(selected_anime)],
            random_source,
        )

        if chosen_anime is None:
            return None

        selected_value = get_comparison_value(chosen_anime, category["metric"])
        available_candidates.remove(chosen_anime)
        selected_anime.append(chosen_anime)

        previous_value = selected_value
        previous_anime = chosen_anime

    if rating_context is None:
        return selected_anime

    return order_category_for_opening(
        selected_anime,
        category,
        recent_matchup_pairs,
        random_source,
        rating_context,
        series_representative_popularity_ranks,
    )


def try_generate_challenge(
    catalog,
    recent_anime_ids_by_category,
    recent_matchup_pairs,
    random_source,
    series_representative_ids=None,
    rating_context=None,
    series_representative_popularity_ranks=None,
):
    for _ in range(MAX_GENERATION_ATTEMPTS):
        used_anime_ids = set()
        selected_by_category = {}

        for category in CATEGORY_RULES:
            selected_anime = select_category_anime(
                catalog,
                category,
                used_anime_ids,
                recent_anime_ids_by_category.get(category["name"], set()),
                recent_matchup_pairs,
                random_source,
                series_representative_ids,
                rating_context,
                series_representative_popularity_ranks,
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
    recent_anime_ids_by_category=None,
    recent_matchup_pairs=None,
    series_representative_ids=None,
    rating_context=None,
    series_representative_popularity_ranks=None,
):
    if random_source is None:
        random_source = random

    if recent_anime_ids_by_category is None:
        recent_anime_ids_by_category = {}

    if recent_matchup_pairs is None:
        recent_matchup_pairs = set()

    unique_anime_ids = {
        anime.get("mal_id") for anime in catalog if anime.get("mal_id") is not None
    }

    if len(unique_anime_ids) < ANIME_PER_CATEGORY * len(CATEGORY_RULES):
        raise RuntimeError(
            f"The catalog needs at least {ANIME_PER_CATEGORY * len(CATEGORY_RULES)} "
            "unique MAL entries to build a complete challenge."
        )

    if rating_context is None:
        from challenge_ratings import build_rating_context

        rating_context = build_rating_context(catalog)

    challenge = try_generate_challenge(
        catalog,
        recent_anime_ids_by_category,
        recent_matchup_pairs,
        random_source,
        series_representative_ids,
        rating_context,
        series_representative_popularity_ranks,
    )

    if challenge is not None:
        return challenge

    raise RuntimeError(
        "The catalog could not produce a complete valid challenge. Factual "
        "validity, canonical More Episodes representatives, category-specific "
        "recent-anime cooldowns, unique MAL IDs, and recent exact-matchup "
        "prevention remain required."
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

    expected_categories = {category["name"] for category in CATEGORY_RULES}
    if set(placements_by_category) != expected_categories:
        raise LegacyChallengeError(
            "This stored challenge uses an older category format and cannot be "
            "played in the current four-round game. Its official result remains "
            "available in the archive."
        )

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


def load_stored_challenge(
    challenge_date,
    database_path=DATABASE_PATH,
    include_drafts=False,
):
    challenge_record = load_challenge_record(
        challenge_date, database_path, include_drafts=include_drafts
    )

    if challenge_record is None:
        return None

    return challenge_from_record(challenge_record)


def load_series_display_metadata(catalog, database_path):
    """Return selectable canonical series IDs and their popularity ranks."""
    episodic_ids = [
        anime["mal_id"]
        for anime in catalog
        if anime.get("type") in EPISODIC_MEDIA_TYPES
        and anime.get("series_episodes") is not None
    ]
    representatives = load_series_representatives(episodic_ids, database_path)
    representative_ids = {
        anime_id
        for anime_id, representative in representatives.items()
        if representative is not None
        and representative["mal_id"] == anime_id
    }
    catalog_by_id = {anime["mal_id"]: anime for anime in catalog}
    representative_popularity_ranks = {
        anime_id: catalog_by_id[anime_id].get("popularity_rank")
        for anime_id in representative_ids
    }
    return representative_ids, representative_popularity_ranks


def load_recent_category_usage(challenge_date, database_path):
    recent_by_category = load_recent_category_anime_ids(
        challenge_date,
        RECENT_ANIME_DAYS,
        database_path,
    )
    recent_series_ids = recent_by_category.get("More Episodes", set())
    if recent_series_ids:
        representatives = load_series_representatives(
            recent_series_ids,
            database_path,
        )
        recent_by_category["More Episodes"] = {
            representative["mal_id"] if representative else anime_id
            for anime_id, representative in representatives.items()
        }
    return recent_by_category


def generate_history_aware_challenge(
    challenge_date,
    database_path=DATABASE_PATH,
    random_source=None,
):
    catalog = load_anime_records(database_path)
    series_representative_ids, representative_popularity_ranks = load_series_display_metadata(
        catalog, database_path
    )
    recent_anime_ids_by_category = load_recent_category_usage(
        challenge_date, database_path
    )
    recent_matchup_pairs = load_recent_matchup_pairs(
        challenge_date,
        RECENT_MATCHUP_DAYS,
        database_path,
    )

    return generate_challenge(
        catalog,
        random_source,
        recent_anime_ids_by_category,
        recent_matchup_pairs,
        series_representative_ids,
        series_representative_popularity_ranks=representative_popularity_ranks,
    )


def challenge_signature(challenge):
    return tuple(
        anime["mal_id"]
        for category in challenge
        for anime in category["anime"]
    )


def generate_challenge_candidates(
    challenge_date,
    count=20,
    database_path=DATABASE_PATH,
    random_source=None,
):
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise ValueError("count must be a positive integer.")

    if random_source is None:
        random_source = random

    catalog = load_anime_records(database_path)
    series_representative_ids, representative_popularity_ranks = load_series_display_metadata(
        catalog, database_path
    )
    recent_anime_ids_by_category = load_recent_category_usage(
        challenge_date, database_path
    )
    recent_matchup_pairs = load_recent_matchup_pairs(
        challenge_date,
        RECENT_MATCHUP_DAYS,
        database_path,
    )

    # Imported here because challenge_ratings uses the category rules above.
    from challenge_ratings import build_rating_context, rate_challenge

    rating_context = build_rating_context(catalog)
    candidates = []
    signatures = set()
    attempts = 0
    maximum_attempts = count * MAX_BATCH_ATTEMPTS_PER_CANDIDATE

    while len(candidates) < count and attempts < maximum_attempts:
        attempts += 1
        challenge = generate_challenge(
            catalog,
            random_source,
            recent_anime_ids_by_category,
            recent_matchup_pairs,
            series_representative_ids,
            rating_context=rating_context,
            series_representative_popularity_ranks=representative_popularity_ranks,
        )
        signature = challenge_signature(challenge)

        if signature in signatures:
            continue

        signatures.add(signature)
        candidate_number = len(candidates) + 1
        candidates.append(
            {
                "candidate_id": f"candidate_{candidate_number:02d}",
                "challenge_date": str(challenge_date),
                "categories": challenge,
                "ratings": rate_challenge(
                    challenge,
                    rating_context=rating_context,
                ),
            }
        )

    if len(candidates) < count:
        raise RuntimeError(
            f"Could only generate {len(candidates)} unique challenge candidates "
            f"after {maximum_attempts} attempts."
        )

    return candidates


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


def serialize_public_challenge(
    challenge_date,
    challenge,
    database_path=DATABASE_PATH,
):
    categories = []
    series_anime_ids = [
        anime["mal_id"]
        for category in challenge
        if category["name"] == "More Episodes"
        for anime in category["anime"]
    ]
    series_representatives = load_series_representatives(
        series_anime_ids,
        database_path,
    )

    for category in challenge:
        public_anime = []

        for position, anime in enumerate(category["anime"], start=1):
            title = anime["title"]
            image_url = anime.get("image_url")

            if category["name"] == "More Episodes":
                representative = series_representatives[anime["mal_id"]]
                title = (
                    representative["title"]
                    if representative
                    else "Series representative unavailable"
                )
                image_url = representative["image_url"] if representative else None

            public_anime.append(
                {
                    "position": position,
                    "mal_id": anime["mal_id"],
                    "title": title,
                    "type": anime["type"],
                    "image_url": image_url,
                }
            )
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


def validate_public_challenge(
    challenge,
    challenge_date,
    database_path=DATABASE_PATH,
):
    """Validate the same factual/public invariants used before API serving."""
    errors = []
    expected_names = [rule["name"] for rule in CATEGORY_RULES]

    if not isinstance(challenge, list) or len(challenge) != len(CATEGORY_RULES):
        errors.append("challenge must contain exactly four categories")

    categories_by_name = {
        category.get("name"): category
        for category in challenge
        if isinstance(category, dict)
    } if isinstance(challenge, list) else {}
    if list(categories_by_name) != expected_names:
        errors.append("category order or names do not match the current game")

    catalog = {
        anime["mal_id"]: anime
        for anime in load_anime_records(database_path)
        if anime.get("mal_id") is not None
    }
    more_episodes = categories_by_name.get("More Episodes")
    series_representatives = load_series_representatives(
        [
            anime.get("mal_id")
            for anime in (more_episodes.get("anime") or [])
            if isinstance(anime, dict) and anime.get("mal_id") is not None
        ] if more_episodes else [],
        database_path,
    )
    seen_ids = set()

    for rule in CATEGORY_RULES:
        category = categories_by_name.get(rule["name"])
        if category is None:
            continue
        anime_order = category.get("anime")
        if category.get("metric") != rule["metric"]:
            errors.append(f"{rule['name']} uses the wrong comparison metric")
        if not isinstance(anime_order, list) or len(anime_order) != ANIME_PER_CATEGORY:
            errors.append(f"{rule['name']} must contain six anime")
            continue

        for anime in anime_order:
            if not isinstance(anime, dict):
                errors.append(f"{rule['name']} contains an invalid anime record")
                continue
            mal_id = anime.get("mal_id")
            if mal_id in seen_ids:
                errors.append(f"duplicate MAL ID in challenge: {mal_id}")
            seen_ids.add(mal_id)
            if mal_id not in catalog:
                errors.append(f"MAL ID {mal_id} is missing from the catalog")
            if mal_id in KNOWN_SYNTHETIC_MAL_IDS:
                errors.append(f"known synthetic fixture MAL ID: {mal_id}")
            if SYNTHETIC_TITLE_PATTERN.fullmatch(anime.get("title", "")):
                errors.append(f"synthetic fixture title: {anime['title']}")
            if not has_eligible_display_type(anime, rule):
                errors.append(f"unsupported media type in {rule['name']}")
            if get_comparison_value(anime, rule["metric"]) is None:
                errors.append(f"missing {rule['metric']} value in {rule['name']}")
            if (
                rule["name"] == "More Popular"
                and anime.get("popularity_rank") is None
            ):
                errors.append("More Popular entries require popularity_rank")
            if rule["name"] == "More Episodes":
                representative = series_representatives.get(mal_id)
                if representative is None:
                    errors.append(
                        f"MAL ID {mal_id} has no verified series representative"
                    )
                elif representative["mal_id"] != mal_id:
                    errors.append(
                        f"MAL ID {mal_id} is not its canonical series representative"
                    )

        for anime_a, anime_b in zip(anime_order, anime_order[1:]):
            value_a = get_comparison_value(anime_a, rule["metric"])
            value_b = get_comparison_value(anime_b, rule["metric"])
            if value_a is None or value_b is None or value_a == value_b:
                errors.append(f"invalid tied/missing matchup in {rule['name']}")

    if errors:
        raise PublicChallengeValidationError("; ".join(dict.fromkeys(errors)))

    try:
        public_payload = serialize_public_challenge(
            challenge_date,
            challenge,
            database_path,
        )
    except Exception as error:
        raise PublicChallengeValidationError(
            f"public serialization failed: {error}"
        ) from error

    if any(
        SYNTHETIC_TITLE_PATTERN.fullmatch(anime.get("title", ""))
        for category in public_payload["categories"]
        for anime in category["anime"]
    ):
        raise PublicChallengeValidationError("public payload contains synthetic fixture data")

    return public_payload


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


def verify_completed_answers(challenge, answers):
    expected_comparisons = {
        (category["name"], comparison_position)
        for category in challenge
        for comparison_position in range(1, len(category["anime"]))
    }

    if len(answers) != len(expected_comparisons):
        raise ValueError(
            f"A completed challenge must contain {len(expected_comparisons)} answers."
        )

    answered_comparisons = set()
    verified_score = 0

    for answer in answers:
        try:
            category_name = answer["category"]
            comparison_position = answer["comparison_position"]
            selected_mal_id = answer["selected_mal_id"]
        except (KeyError, TypeError) as error:
            raise ValueError(
                "Every answer must identify one comparison and choice."
            ) from error

        comparison_key = (category_name, comparison_position)

        if comparison_key not in expected_comparisons:
            raise ValueError("The completion contains an unknown comparison.")

        if comparison_key in answered_comparisons:
            raise ValueError("Each comparison may appear only once in a completion.")

        result = evaluate_comparison(
            challenge,
            category_name,
            comparison_position,
            selected_mal_id,
        )
        answered_comparisons.add(comparison_key)

        if result["correct"]:
            verified_score += 1

    if answered_comparisons != expected_comparisons:
        raise ValueError("The completion does not include every challenge comparison.")

    return verified_score
