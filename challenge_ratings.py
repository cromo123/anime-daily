import math
from bisect import bisect_left, bisect_right
from datetime import date

from challenge import (
    CATEGORY_RULES,
    DISPLAY_MEDIA_TYPES,
    get_comparison_value,
    is_eligible,
)
from database import DATABASE_PATH, load_anime_records


RATING_DECIMAL_PLACES = 2
CATEGORY_METRICS = {
    category["name"]: category["metric"] for category in CATEGORY_RULES
}


def rating_value(anime, metric):
    value = get_comparison_value(anime, metric)

    if isinstance(value, date):
        return value.toordinal()

    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            return None

        return value

    return None


def sorted_rating_values(anime_records, metric):
    values = []

    for anime in anime_records:
        value = rating_value(anime, metric)

        if value is not None:
            values.append(value)

    return sorted(values)


def build_rating_context(catalog):
    catalog_by_id = {
        anime["mal_id"]: anime
        for anime in catalog
        if anime.get("mal_id") is not None
    }
    anime_records = list(catalog_by_id.values())
    display_records = [
        anime
        for anime in anime_records
        if anime.get("type") in DISPLAY_MEDIA_TYPES
    ]
    difficulty_distributions = {}

    for category in CATEGORY_RULES:
        eligible_records = [
            anime
            for anime in anime_records
            if is_eligible(anime, category, set(), set())
        ]
        difficulty_distributions[category["name"]] = sorted_rating_values(
            eligible_records,
            category["metric"],
        )

    return {
        "members": sorted_rating_values(display_records, "members"),
        "release_date": sorted_rating_values(display_records, "release_date"),
        "difficulty": difficulty_distributions,
    }


def empirical_percentile(value, sorted_values):
    if value is None or not sorted_values:
        return 0.0

    lower_index = bisect_left(sorted_values, value)
    upper_index = bisect_right(sorted_values, value)
    equal_values = upper_index - lower_index
    midpoint_rank = lower_index + equal_values / 2
    percentile = midpoint_rank / len(sorted_values) * 100
    return min(100.0, max(0.0, percentile))


def average_score(scores):
    if not scores:
        return 0.0

    return sum(scores) / len(scores)


def comparison_difficulty(anime_a, anime_b, category_name, rating_context):
    metric = CATEGORY_METRICS.get(category_name)

    if metric is None:
        raise ValueError(f"Unknown category: {category_name}")

    value_a = rating_value(anime_a, metric)
    value_b = rating_value(anime_b, metric)
    distribution = rating_context["difficulty"].get(category_name, [])

    if value_a is None or value_b is None or not distribution:
        return 0.0

    percentile_a = empirical_percentile(value_a, distribution)
    percentile_b = empirical_percentile(value_b, distribution)
    return 100 - abs(percentile_a - percentile_b)


def rounded_score(score):
    return round(min(100.0, max(0.0, score)), RATING_DECIMAL_PLACES)


def rate_challenge(
    challenge,
    catalog=None,
    rating_context=None,
    database_path=DATABASE_PATH,
):
    if rating_context is None:
        if catalog is None:
            catalog = load_anime_records(database_path)

        rating_context = build_rating_context(catalog)

    category_ratings = []
    all_popularity_scores = []
    all_modernity_scores = []
    all_difficulty_scores = []

    for category in challenge:
        category_name = category["name"]

        if category_name not in CATEGORY_METRICS:
            raise ValueError(f"Unknown category: {category_name}")

        popularity_scores = []
        modernity_scores = []

        for anime in category.get("anime", []):
            popularity_percentile = empirical_percentile(
                rating_value(anime, "members"),
                rating_context["members"],
            )
            modernity_percentile = empirical_percentile(
                rating_value(anime, "release_date"),
                rating_context["release_date"],
            )
            popularity_scores.append(popularity_percentile)
            modernity_scores.append(modernity_percentile)

        comparison_ratings = []
        difficulty_scores = []
        anime_list = category.get("anime", [])

        for index in range(len(anime_list) - 1):
            difficulty_score = comparison_difficulty(
                anime_list[index],
                anime_list[index + 1],
                category_name,
                rating_context,
            )
            difficulty_scores.append(difficulty_score)
            comparison_ratings.append(
                {
                    "position": index + 1,
                    "difficulty_score": rounded_score(difficulty_score),
                }
            )

        category_ratings.append(
            {
                "name": category_name,
                "popularity_score": rounded_score(
                    average_score(popularity_scores)
                ),
                "difficulty_score": rounded_score(
                    average_score(difficulty_scores)
                ),
                "modernity_score": rounded_score(
                    average_score(modernity_scores)
                ),
                "comparisons": comparison_ratings,
            }
        )
        all_popularity_scores.extend(popularity_scores)
        all_modernity_scores.extend(modernity_scores)
        all_difficulty_scores.extend(difficulty_scores)

    return {
        "popularity_score": rounded_score(
            average_score(all_popularity_scores)
        ),
        "difficulty_score": rounded_score(
            average_score(all_difficulty_scores)
        ),
        "modernity_score": rounded_score(
            average_score(all_modernity_scores)
        ),
        "categories": category_ratings,
    }
