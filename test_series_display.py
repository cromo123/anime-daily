import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database as database_module

from challenge import (
    CATEGORY_RULES,
    evaluate_category_comparison,
    is_eligible,
    load_series_display_metadata,
    serialize_public_challenge,
)
from database import (
    load_series_representatives,
    resolve_and_store_series_episode_count,
    store_anime_relations,
    upsert_anime_records,
)


def anime(mal_id, title, release_date, media_type="tv", episodes=12, series=12):
    return {
        "mal_id": mal_id,
        "title": title,
        "image_url": f"https://example.test/{mal_id}.jpg",
        "type": media_type,
        "release_date": release_date,
        "entry_episodes": episodes,
        "series_episodes": series,
        "score": 8.0,
        "popularity_rank": mal_id,
        "members": 1000 + mal_id,
    }


class SeriesDisplayTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = Path(self.temporary_directory.name) / "anime_daily.db"

        self.first_season = anime(
            16498, "Shingeki no Kyojin", "2013-04-07", episodes=25, series=89
        )
        self.season_two = anime(
            25777, "Shingeki no Kyojin Season 2", "2017-04-01", episodes=12, series=89
        )
        self.season_three = anime(
            35760, "Shingeki no Kyojin Season 3", "2018-07-23", episodes=22, series=89
        )
        self.standalone = anime(
            999, "Standalone Anime", "2010-01-01", episodes=12, series=12
        )
        upsert_anime_records(
            [self.first_season, self.season_two, self.season_three, self.standalone],
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

    def test_only_first_release_is_selectable_series_representative(self):
        representatives = load_series_representatives(
            [35760, 16498, 999], self.database_path
        )
        self.assertEqual(representatives[35760]["mal_id"], 16498)
        self.assertEqual(representatives[16498]["mal_id"], 16498)
        self.assertEqual(representatives[999]["mal_id"], 999)

        catalog = [self.first_season, self.season_two, self.season_three, self.standalone]
        representative_ids, _ = load_series_display_metadata(
            catalog, self.database_path
        )
        self.assertIn(16498, representative_ids)
        self.assertIn(999, representative_ids)
        self.assertNotIn(25777, representative_ids)
        self.assertNotIn(35760, representative_ids)

        episodes_rule = next(
            rule for rule in CATEGORY_RULES if rule["name"] == "More Episodes"
        )
        self.assertTrue(
            is_eligible(
                self.first_season, episodes_rule, set(), set(), representative_ids
            )
        )
        self.assertFalse(
            is_eligible(
                self.season_three, episodes_rule, set(), set(), representative_ids
            )
        )

        episodes_category = {
            **episodes_rule,
            "anime": [self.first_season, self.standalone],
        }
        score_category = {**CATEGORY_RULES[0], "anime": [self.season_three]}
        public = serialize_public_challenge(
            "2026-09-13",
            [episodes_category, score_category],
            self.database_path,
        )
        displayed_series = public["categories"][0]["anime"][0]
        self.assertEqual(displayed_series["mal_id"], 16498)
        self.assertEqual(displayed_series["title"], self.first_season["title"])
        self.assertEqual(displayed_series["image_url"], self.first_season["image_url"])
        self.assertEqual(
            public["categories"][1]["anime"][0]["title"],
            self.season_three["title"],
        )

        result = evaluate_category_comparison(episodes_category, 1, 16498)
        self.assertTrue(result["correct"])
        self.assertEqual(result["revealed_anime"][0]["series_episodes"], 89)

    def test_release_order_wins_over_story_chronology(self):
        movie = anime(9101, "Story-First Movie", "2016-01-08", "movie", 1, 40)
        later_special = anime(
            9102, "Chronological TV Special", "2012-12-31", "tv_special", 4, 40
        )
        first_released_tv = anime(
            9103, "First Released Main TV", "2009-07-03", "tv", 15, 40
        )
        upsert_anime_records(
            [movie, later_special, first_released_tv], self.database_path
        )
        store_anime_relations(
            9101, [{"mal_id": 9102, "relation_type": "sequel"}], self.database_path
        )
        store_anime_relations(
            9102,
            [
                {"mal_id": 9101, "relation_type": "prequel"},
                {"mal_id": 9103, "relation_type": "sequel"},
            ],
            self.database_path,
        )
        store_anime_relations(
            9103, [{"mal_id": 9102, "relation_type": "prequel"}], self.database_path
        )

        representatives = load_series_representatives(
            [9101, 9102, 9103], self.database_path
        )
        self.assertEqual(representatives[9101]["mal_id"], 9103)
        self.assertEqual(representatives[9102]["mal_id"], 9103)
        self.assertEqual(representatives[9103]["mal_id"], 9103)

    def test_derivative_children_are_not_series_candidates_or_contributors(self):
        main_one = anime(9201, "Main TV", "2013-01-01", episodes=25, series=37)
        main_two = anime(9202, "Main TV 2", "2017-01-01", episodes=12, series=37)
        side_ova = anime(9203, "Side OVA", "2014-01-01", "ova", 3, 3)
        recap = anime(9204, "Recap", "2018-01-01", "special", 1, 1)
        upsert_anime_records(
            [main_one, main_two, side_ova, recap], self.database_path
        )
        store_anime_relations(
            9201,
            [
                {"mal_id": 9202, "relation_type": "sequel"},
                {"mal_id": 9203, "relation_type": "side_story"},
                {"mal_id": 9204, "relation_type": "summary"},
            ],
            self.database_path,
        )
        store_anime_relations(
            9202, [{"mal_id": 9201, "relation_type": "prequel"}], self.database_path
        )
        store_anime_relations(
            9203, [{"mal_id": 9201, "relation_type": "parent_story"}], self.database_path
        )
        store_anime_relations(
            9204, [{"mal_id": 9201, "relation_type": "full_story"}], self.database_path
        )

        representatives = load_series_representatives(
            [9201, 9202, 9203, 9204], self.database_path
        )
        self.assertEqual(representatives[9201]["mal_id"], 9201)
        self.assertEqual(representatives[9202]["mal_id"], 9201)
        self.assertIsNone(representatives[9203])
        self.assertIsNone(representatives[9204])
        self.assertEqual(
            resolve_and_store_series_episode_count(9201, self.database_path), 37
        )
        self.assertEqual(
            resolve_and_store_series_episode_count(9203, self.database_path), 3
        )

        representative_ids, _ = load_series_display_metadata(
            [main_one, main_two, side_ova, recap], self.database_path
        )
        self.assertEqual(representative_ids, {9201})

    def test_incomplete_or_cyclic_graph_has_no_representative(self):
        unresolved = anime(7001, "Unresolved Season", "2020-01-01")
        upsert_anime_records([unresolved], self.database_path)
        self.assertIsNone(
            load_series_representatives([7001], self.database_path)[7001]
        )

        cycle_one = anime(9001, "Cycle One", "2020-01-01")
        cycle_two = anime(9002, "Cycle Two", "2021-01-01")
        upsert_anime_records([cycle_one, cycle_two], self.database_path)
        store_anime_relations(
            9001, [{"mal_id": 9002, "relation_type": "sequel"}], self.database_path
        )
        store_anime_relations(
            9002, [{"mal_id": 9001, "relation_type": "sequel"}], self.database_path
        )
        self.assertIsNone(
            load_series_representatives([9001], self.database_path)[9001]
        )

    def test_representatives_use_two_bulk_queries_and_process_cache(self):
        database_module._invalidate_series_representative_cache(self.database_path)
        statements = []
        connection_count = 0

        def tracked_connection(database_path):
            nonlocal connection_count
            connection_count += 1
            connection = sqlite3.connect(database_path)
            connection.set_trace_callback(statements.append)
            return connection

        requested_ids = [
            self.first_season["mal_id"],
            self.season_two["mal_id"],
            self.season_three["mal_id"],
            self.standalone["mal_id"],
        ]
        with (
            patch("database.initialize_database"),
            patch("database._connect_database", side_effect=tracked_connection),
        ):
            first = load_series_representatives(
                requested_ids, self.database_path
            )
            second = load_series_representatives(
                list(reversed(requested_ids)), self.database_path
            )

        select_statements = [
            statement
            for statement in statements
            if statement.lstrip().upper().startswith("SELECT")
        ]
        self.assertEqual(len(select_statements), 2)
        self.assertEqual(connection_count, 1)
        self.assertEqual(first[self.season_three["mal_id"]]["mal_id"], 16498)
        self.assertEqual(second[self.season_three["mal_id"]]["mal_id"], 16498)


if __name__ == "__main__":
    unittest.main()
