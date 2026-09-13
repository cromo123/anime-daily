import tempfile
import unittest
from pathlib import Path

from challenge import (
    CATEGORY_RULES,
    evaluate_category_comparison,
    is_eligible,
    serialize_public_challenge,
)
from database import (
    load_series_display_roots,
    store_anime_relations,
    upsert_anime_records,
)


class SeriesDisplayTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = Path(self.temporary_directory.name) / "anime_daily.db"

        self.root = {
            "mal_id": 16498,
            "title": "Shingeki no Kyojin",
            "image_url": "https://example.test/season-one.jpg",
            "type": "tv",
            "series_episodes": 89,
        }
        self.season_two = {
            "mal_id": 25777,
            "title": "Shingeki no Kyojin Season 2",
            "image_url": "https://example.test/season-two.jpg",
            "type": "tv",
            "series_episodes": 89,
        }
        self.season_three = {
            "mal_id": 35760,
            "title": "Shingeki no Kyojin Season 3",
            "image_url": "https://example.test/season-three.jpg",
            "type": "tv",
            "series_episodes": 89,
        }
        self.standalone_root = {
            "mal_id": 999,
            "title": "Standalone Anime",
            "image_url": "https://example.test/standalone.jpg",
            "type": "tv",
            "series_episodes": 12,
        }
        upsert_anime_records(
            [self.root, self.season_two, self.season_three, self.standalone_root],
            self.database_path,
        )
        store_anime_relations(
            16498,
            [{"mal_id": 25777, "relation_type": "sequel"}],
            self.database_path,
        )
        store_anime_relations(
            25777,
            [
                {"mal_id": 16498, "relation_type": "prequel"},
                {"mal_id": 35760, "relation_type": "sequel"},
            ],
            self.database_path,
        )
        store_anime_relations(
            35760,
            [{"mal_id": 25777, "relation_type": "prequel"}],
            self.database_path,
        )
        store_anime_relations(999, [], self.database_path)

    def test_sequel_and_root_share_first_entry_display_identity(self):
        roots = load_series_display_roots(
            [35760, 16498, 999],
            self.database_path,
        )
        self.assertEqual(roots[35760]["mal_id"], 16498)
        self.assertEqual(roots[16498]["mal_id"], 16498)
        self.assertEqual(roots[999]["mal_id"], 999)

        episodes_category = {
            "name": "More Episodes",
            "metric": "series_episodes",
            "question": "Which anime series has more episodes?",
            "anime": [self.season_three, self.standalone_root],
        }
        score_category = {
            "name": "Higher Score",
            "metric": "score",
            "question": "Which anime has the higher score?",
            "anime": [self.season_three],
        }
        public = serialize_public_challenge(
            "2026-09-13",
            [episodes_category, score_category],
            self.database_path,
        )
        displayed_sequel = public["categories"][0]["anime"][0]
        self.assertEqual(displayed_sequel["mal_id"], 35760)
        self.assertEqual(displayed_sequel["title"], self.root["title"])
        self.assertEqual(displayed_sequel["image_url"], self.root["image_url"])
        self.assertEqual(
            public["categories"][1]["anime"][0]["title"],
            self.season_three["title"],
        )
        self.assertEqual(
            public["categories"][1]["anime"][0]["image_url"],
            self.season_three["image_url"],
        )

        result = evaluate_category_comparison(episodes_category, 1, 35760)
        self.assertTrue(result["correct"])
        self.assertEqual(result["revealed_anime"][0]["series_episodes"], 89)

    def test_incomplete_graph_does_not_claim_sequel_is_root(self):
        unresolved_sequel = {
            "mal_id": 7001,
            "title": "Unresolved Season 2",
            "image_url": "https://example.test/unresolved-season.jpg",
            "type": "tv",
        }
        upsert_anime_records(
            [unresolved_sequel],
            self.database_path,
        )
        roots = load_series_display_roots([7001], self.database_path)
        self.assertIsNone(roots[7001])
        public = serialize_public_challenge(
            "2026-09-13",
            [
                {
                    "name": "More Episodes",
                    "question": "Which series?",
                    "anime": [unresolved_sequel],
                }
            ],
            self.database_path,
        )
        displayed = public["categories"][0]["anime"][0]
        self.assertEqual(displayed["title"], "Series root unavailable")
        self.assertIsNone(displayed["image_url"])
        self.assertEqual(displayed["mal_id"], 7001)

    def test_multiple_roots_are_not_guessed_from_titles(self):
        upsert_anime_records(
            [
                {"mal_id": 8001, "title": "First Possible Root", "type": "tv"},
                {"mal_id": 8002, "title": "Second Possible Root", "type": "tv"},
                {"mal_id": 8003, "title": "Shared Sequel", "type": "tv"},
            ],
            self.database_path,
        )
        store_anime_relations(
            8001,
            [{"mal_id": 8003, "relation_type": "sequel"}],
            self.database_path,
        )
        store_anime_relations(
            8002,
            [{"mal_id": 8003, "relation_type": "sequel"}],
            self.database_path,
        )
        store_anime_relations(8003, [], self.database_path)

        roots = load_series_display_roots([8003], self.database_path)
        self.assertIsNone(roots[8003])

    def test_unresolved_identity_is_excluded_only_from_more_episodes(self):
        anime = {"mal_id": 7001, "type": "tv", "score": 8.0, "series_episodes": 24}
        episodes_category = next(
            rule for rule in CATEGORY_RULES if rule["name"] == "More Episodes"
        )
        score_category = CATEGORY_RULES[0]

        self.assertFalse(is_eligible(anime, episodes_category, set(), set(), set()))
        self.assertTrue(is_eligible(anime, score_category, set(), set(), set()))

    def test_cyclic_prequel_sequel_graph_has_no_claimed_root(self):
        upsert_anime_records(
            [
                {"mal_id": 9001, "title": "Cycle One", "type": "tv"},
                {"mal_id": 9002, "title": "Cycle Two", "type": "tv"},
            ],
            self.database_path,
        )
        store_anime_relations(
            9001,
            [{"mal_id": 9002, "relation_type": "sequel"}],
            self.database_path,
        )
        store_anime_relations(
            9002,
            [{"mal_id": 9001, "relation_type": "sequel"}],
            self.database_path,
        )

        self.assertIsNone(
            load_series_display_roots([9001], self.database_path)[9001]
        )


if __name__ == "__main__":
    unittest.main()
