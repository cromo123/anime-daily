from anime_data import anime_list


def play_comparison_round(anime_a, anime_b, metric, metric_label, question):
    print(f"1. {anime_a['title']}")
    print(f"2. {anime_b['title']}")

    guess = input(f"{question} 1 or 2: ")

    while guess not in ["1", "2"]:
        print("Please enter 1 or 2.")
        guess = input(f"{question} 1 or 2: ")

    if anime_a[metric] > anime_b[metric]:
        correct_answer = "1"
    else:
        correct_answer = "2"

    is_correct = guess == correct_answer

    if is_correct:
        print("Correct!")
    else:
        print("Wrong!")

    print(f"{anime_a['title']} - {metric_label}: {anime_a[metric]}")
    print(f"{anime_b['title']} - {metric_label}: {anime_b[metric]}")

    return is_correct


def play_category(category, total_score):
    category_score = 0
    category_anime = category["anime"]

    print(f"\n=== {category['name']} ===")

    for index in range(len(category_anime) - 1):
        anime_a = category_anime[index]
        anime_b = category_anime[index + 1]

        print(f"\nComparison {index + 1} of 5")

        is_correct = play_comparison_round(
            anime_a,
            anime_b,
            category["metric"],
            category["metric_label"],
            category["question"],
        )

        if is_correct:
            category_score += 1
            total_score += 1

        print(f"Total score: {total_score}")

    print(f"\n{category['name']} result: {category_score} / 5")

    return total_score


categories = [
    {
        "name": "Higher Score",
        "metric": "score",
        "metric_label": "Score",
        "question": "Which anime has the higher score?",
        "anime": anime_list[0:6],
    },
    {
        "name": "More Popular",
        "metric": "members",
        "metric_label": "Members",
        "question": "Which anime is more popular?",
        "anime": anime_list[6:12],
    },
    {
        "name": "More Episodes",
        "metric": "episodes",
        "metric_label": "Episodes",
        "question": "Which anime has more episodes?",
        "anime": anime_list[12:18],
    },
    {
        "name": "More Recent",
        "metric": "year",
        "metric_label": "Release year",
        "question": "Which anime is more recent?",
        "anime": anime_list[18:24],
    },
    {
        "name": "Longer Runtime",
        "metric": "runtime_minutes",
        "metric_label": "Runtime in minutes",
        "question": "Which movie has the longer runtime?",
        "anime": anime_list[24:30],
    },
]

total_score = 0

for category in categories:
    total_score = play_category(category, total_score)

print(f"\nFinal score: {total_score} / 25")
