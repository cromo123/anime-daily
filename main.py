import random
from datetime import date

from database import load_anime_records


ANIME_PER_CATEGORY = 6
MAX_GENERATION_ATTEMPTS = 500

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


def is_eligible(anime, category, used_anime_ids):
    mal_id = anime.get("mal_id")

    if mal_id is None or mal_id in used_anime_ids:
        return False

    if category["name"] == "Longer Runtime" and anime.get("type") != "movie":
        return False

    if category["name"] == "More Popular" and anime.get("popularity_rank") is None:
        return False

    return get_comparison_value(anime, category["metric"]) is not None


def select_category_anime(catalog, category, used_anime_ids, random_source):
    candidates_by_id = {}

    for anime in catalog:
        if is_eligible(anime, category, used_anime_ids):
            candidates_by_id[anime["mal_id"]] = anime

    candidates_by_value = {}

    for anime in candidates_by_id.values():
        value = get_comparison_value(anime, category["metric"])
        candidates_by_value.setdefault(value, []).append(anime)

    for candidates in candidates_by_value.values():
        random_source.shuffle(candidates)

    selected_anime = []
    previous_value = None

    while len(selected_anime) < ANIME_PER_CATEGORY:
        available_values = [
            value
            for value, candidates in candidates_by_value.items()
            if candidates and value != previous_value
        ]

        if not available_values:
            return None

        largest_group_size = max(
            len(candidates_by_value[value]) for value in available_values
        )
        largest_values = [
            value
            for value in available_values
            if len(candidates_by_value[value]) == largest_group_size
        ]
        selected_value = random_source.choice(largest_values)
        selected_anime.append(candidates_by_value[selected_value].pop())
        previous_value = selected_value

    return selected_anime


def generate_challenge(catalog, random_source=None):
    if random_source is None:
        random_source = random

    unique_anime_ids = {
        anime.get("mal_id") for anime in catalog if anime.get("mal_id") is not None
    }

    if len(unique_anime_ids) < ANIME_PER_CATEGORY * len(CATEGORY_RULES):
        raise RuntimeError(
            "The catalog needs at least 30 unique MAL entries to build a complete "
            "challenge."
        )

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

    raise RuntimeError(
        "The catalog could not produce a complete valid challenge. It needs six "
        "eligible anime per category, unequal adjacent metric values, six movies "
        "for Longer Runtime, and 30 MAL IDs with no cross-category reuse."
    )


def play_comparison_round(anime_a, anime_b, category):
    metric = category["metric"]
    value_a = get_comparison_value(anime_a, metric)
    value_b = get_comparison_value(anime_b, metric)

    if value_a is None or value_b is None or value_a == value_b:
        raise RuntimeError("The generated challenge contains an invalid matchup.")

    print(f"1. {anime_a['title']}")
    print(f"2. {anime_b['title']}")

    guess = input(f"{category['question']} 1 or 2: ")

    while guess not in ["1", "2"]:
        print("Please enter 1 or 2.")
        guess = input(f"{category['question']} 1 or 2: ")

    if value_a > value_b:
        correct_answer = "1"
    else:
        correct_answer = "2"

    is_correct = guess == correct_answer

    if is_correct:
        print("Correct!")
    else:
        print("Wrong!")

    if category["name"] == "More Popular":
        print(
            f"{anime_a['title']} - Popularity rank: "
            f"{anime_a['popularity_rank']}, Members: {anime_a['members']}"
        )
        print(
            f"{anime_b['title']} - Popularity rank: "
            f"{anime_b['popularity_rank']}, Members: {anime_b['members']}"
        )
    else:
        print(f"{anime_a['title']} - {category['metric_label']}: {anime_a[metric]}")
        print(f"{anime_b['title']} - {category['metric_label']}: {anime_b[metric]}")

    return is_correct


def play_category(category, total_score):
    category_score = 0
    category_anime = category["anime"]

    print(f"\n=== {category['name']} ===")

    for index in range(len(category_anime) - 1):
        anime_a = category_anime[index]
        anime_b = category_anime[index + 1]

        print(f"\nComparison {index + 1} of 5")

        is_correct = play_comparison_round(anime_a, anime_b, category)

        if is_correct:
            category_score += 1
            total_score += 1

        print(f"Total score: {total_score}")

    print(f"\n{category['name']} result: {category_score} / 5")

    return total_score


def main():
    catalog = load_anime_records()
    challenge = generate_challenge(catalog)
    total_score = 0

    for category in challenge:
        total_score = play_category(category, total_score)

    print(f"\nFinal score: {total_score} / 25")


if __name__ == "__main__":
    main()
