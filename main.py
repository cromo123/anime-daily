from datetime import date

from challenge import evaluate_category_comparison, get_or_create_daily_challenge


def play_comparison_round(anime_a, anime_b, category, comparison_position):
    print(f"1. {anime_a['title']}")
    print(f"2. {anime_b['title']}")

    guess = input(f"{category['question']} 1 or 2: ")

    while guess not in ["1", "2"]:
        print("Please enter 1 or 2.")
        guess = input(f"{category['question']} 1 or 2: ")

    selected_anime = anime_a if guess == "1" else anime_b
    result = evaluate_category_comparison(
        category,
        comparison_position,
        selected_anime["mal_id"],
    )

    if result["correct"]:
        print("Correct!")
    else:
        print("Wrong!")

    for revealed_anime in result["revealed_anime"]:
        if category["name"] == "More Popular":
            print(
                f"{revealed_anime['title']} - Popularity rank: "
                f"{revealed_anime['popularity_rank']}, Members: "
                f"{revealed_anime['members']}"
            )
        else:
            metric = category["metric"]
            print(
                f"{revealed_anime['title']} - {category['metric_label']}: "
                f"{revealed_anime[metric]}"
            )

    return result["correct"]


def play_category(category, total_score):
    category_score = 0
    category_anime = category["anime"]

    print(f"\n=== {category['name']} ===")

    for index in range(len(category_anime) - 1):
        anime_a = category_anime[index]
        anime_b = category_anime[index + 1]
        comparison_position = index + 1

        print(f"\nComparison {comparison_position} of 5")

        is_correct = play_comparison_round(
            anime_a,
            anime_b,
            category,
            comparison_position,
        )

        if is_correct:
            category_score += 1
            total_score += 1

        print(f"Total score: {total_score}")

    print(f"\n{category['name']} result: {category_score} / 5")

    return total_score


def main():
    challenge_date = date.today().isoformat()
    challenge = get_or_create_daily_challenge(challenge_date)
    total_score = 0

    for category in challenge:
        total_score = play_category(category, total_score)

    print(f"\nFinal score: {total_score} / 25")


if __name__ == "__main__":
    main()
