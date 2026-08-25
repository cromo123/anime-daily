from anime_data import anime_list

def play_score_round(anime_a, anime_b):
    print(f"1. {anime_a['title']}")
    print(f"2. {anime_b['title']}")

    guess = input("Which anime has the higher score? 1 or 2: ")

    while guess not in ["1", "2"]:
        print("Please enter 1 or 2.")
        guess = input("Which anime has the higher score? 1 or 2: ")

    if anime_a["score"] > anime_b["score"]:
        correct_answer = "1"
    else:
        correct_answer = "2"

    is_correct = guess == correct_answer

    if is_correct:
        print("Correct!")
    else:
        print("Wrong!")

    print(f"{anime_a['title']}: {anime_a['score']}")
    print(f"{anime_b['title']}: {anime_b['score']}")

    return is_correct

score = 0

for index in range(len(anime_list) - 1):
    anime_a = anime_list[index]
    anime_b = anime_list[index + 1]

    print(f"\nRound {index + 1}")

    result = play_score_round(anime_a, anime_b)

    if result:
        score += 1

    print(f"Current score: {score}")

print(f"\nFinal score: {score}")
