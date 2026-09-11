from challenge import POPULAR_RANK_PRIMARY_MAX, POPULAR_RANK_SECONDARY_MAX


def popularity_tier(popularity_rank):
    if popularity_rank is None:
        return "unknown"

    if popularity_rank <= POPULAR_RANK_PRIMARY_MAX:
        return "top_1000"

    if popularity_rank <= POPULAR_RANK_SECONDARY_MAX:
        return "1001_3000"

    return "wildcard"


def build_curator_candidate_context(candidates):
    curator_candidates = []

    for candidate in candidates:
        ratings = candidate["ratings"]
        category_ratings = {
            category["name"]: category
            for category in ratings["categories"]
        }
        curator_categories = []

        for category in candidate["categories"]:
            rating = category_ratings[category["name"]]
            curator_categories.append(
                {
                    "name": category["name"],
                    "popularity_score": rating["popularity_score"],
                    "difficulty_score": rating["difficulty_score"],
                    "modernity_score": rating["modernity_score"],
                    "anime": [
                        {
                            "mal_id": anime["mal_id"],
                            "title": anime["title"],
                            "type": anime["type"],
                            "popularity_tier": popularity_tier(
                                anime.get("popularity_rank")
                            ),
                        }
                        for anime in category["anime"]
                    ],
                }
            )

        curator_candidates.append(
            {
                "candidate_id": candidate["candidate_id"],
                "overall": {
                    "popularity_score": ratings["popularity_score"],
                    "difficulty_score": ratings["difficulty_score"],
                    "modernity_score": ratings["modernity_score"],
                },
                "categories": curator_categories,
            }
        )

    return curator_candidates


def format_rating(value):
    return f"{value:.2f}"


def single_line(value):
    return " ".join(str(value).split())


def format_curator_candidate_context(curator_candidates):
    candidate_sections = []

    for candidate in curator_candidates:
        overall = candidate["overall"]
        lines = [
            candidate["candidate_id"],
            (
                "Overall: "
                f"Popularity {format_rating(overall['popularity_score'])} | "
                f"Difficulty {format_rating(overall['difficulty_score'])} | "
                f"Modernity {format_rating(overall['modernity_score'])}"
            ),
        ]

        for category in candidate["categories"]:
            lines.extend(
                [
                    "",
                    category["name"],
                    (
                        "Ratings: "
                        f"P {format_rating(category['popularity_score'])} | "
                        f"D {format_rating(category['difficulty_score'])} | "
                        f"M {format_rating(category['modernity_score'])}"
                    ),
                ]
            )

            for position, anime in enumerate(category["anime"], start=1):
                lines.append(
                    f"{position}. {single_line(anime['title'])} "
                    f"[MAL {anime['mal_id']}, {anime['type']}, "
                    f"{anime['popularity_tier']}]"
                )

        candidate_sections.append("\n".join(lines))

    return "\n\n---\n\n".join(candidate_sections)
