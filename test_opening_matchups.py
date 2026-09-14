"""The opening preference may reorder a chain, but cannot invalidate it."""

import random
import unittest

from challenge import (
    CATEGORY_RULES,
    has_valid_adjacent_matchups,
    opening_popularity_tier,
    order_category_for_opening,
)
from challenge_ratings import build_rating_context
from database import normalize_matchup_pair


class OpeningMatchupTests(unittest.TestCase):
    def setUp(self):
        self.anime = [
            {
                "mal_id": index,
                "title": f"Anime {index}",
                "type": "tv",
                "score": float(index),
                "members": index * 1000,
                "series_episodes": index * 12,
                "release_date": f"200{index}-01-01",
                "popularity_rank": 5000 if index <= 2 else index * 100,
            }
            for index in range(1, 7)
        ]
        self.context = build_rating_context(self.anime)

    def test_prefers_recognizable_opening_without_sorting_the_chain(self):
        category = CATEGORY_RULES[0]
        ordered = order_category_for_opening(
            self.anime,
            category,
            set(),
            random.Random(7),
            self.context,
        )
        self.assertEqual(
            {anime["mal_id"] for anime in ordered},
            {anime["mal_id"] for anime in self.anime},
        )
        self.assertTrue(all(
            anime["popularity_rank"] <= 1000 for anime in ordered[:2]
        ))
        self.assertTrue(has_valid_adjacent_matchups(ordered, "score", set()))
        self.assertNotEqual(ordered, sorted(ordered, key=lambda anime: anime["score"]))

    def test_history_constraints_leave_a_valid_fallback(self):
        original_pairs = {
            normalize_matchup_pair(a["mal_id"], b["mal_id"])
            for a, b in zip(self.anime, self.anime[1:])
        }
        blocked_pairs = {
            normalize_matchup_pair(a["mal_id"], b["mal_id"])
            for index, a in enumerate(self.anime)
            for b in self.anime[index + 1:]
        } - original_pairs
        ordered = order_category_for_opening(
            self.anime,
            CATEGORY_RULES[0],
            blocked_pairs,
            random.Random(19),
            self.context,
        )
        self.assertTrue(has_valid_adjacent_matchups(
            ordered, "score", blocked_pairs
        ))

    def test_more_episodes_uses_displayed_root_popularity(self):
        sequel = {"mal_id": 9, "popularity_rank": 5000}
        root_ranks = {9: 250}
        self.assertEqual(
            opening_popularity_tier(sequel, "More Episodes", root_ranks),
            "primary",
        )
        self.assertEqual(
            opening_popularity_tier(sequel, "Higher Score", root_ranks),
            "wildcard",
        )

    def test_all_wildcard_lineup_is_still_allowed(self):
        wildcard_anime = [
            {**anime, "popularity_rank": 5000} for anime in self.anime
        ]
        ordered = order_category_for_opening(
            wildcard_anime,
            CATEGORY_RULES[0],
            set(),
            random.Random(4),
            build_rating_context(wildcard_anime),
        )
        self.assertEqual(len(ordered), 6)
        self.assertTrue(has_valid_adjacent_matchups(ordered, "score", set()))


if __name__ == "__main__":
    unittest.main()
