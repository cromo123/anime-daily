import random
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database as database_module

from challenge import (
    CATEGORY_RULES,
    PublicChallengeValidationError,
    generate_challenge,
    is_eligible,
    load_recent_category_usage,
    validate_public_challenge,
)
from database import (
    initialize_database,
    load_recent_category_anime_ids,
    store_anime_relations,
    upsert_anime_records,
)


def anime(mal_id, title, release_date, series_episodes, score=8.0, members=1000):
    return {
        "mal_id": mal_id,
        "title": title,
        "image_url": f"https://example.test/{mal_id}.jpg",
        "type": "tv",
        "release_date": release_date,
        "entry_episodes": 12,
        "series_episodes": series_episodes,
        "score": score,
        "popularity_rank": mal_id,
        "members": members,
    }


class MoreEpisodesRuleTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = Path(self.temporary_directory.name) / "anime_daily.db"
        initialize_database(self.database_path)

        self.category_anime = {}
        next_id = 100
        for category_index, rule in enumerate(CATEGORY_RULES):
            entries = []
            for position in range(6):
                entry = anime(
                    next_id,
                    f"{rule['name']} Anime {position}",
                    f"20{10 + category_index}-{position + 1:02d}-01",
                    12 + category_index * 10 + position,
                    score=7.0 + position / 10,
                    members=1000 + category_index * 100 + position,
                )
                entries.append(entry)
                next_id += 1
            self.category_anime[rule["name"]] = entries

        self.series_representative = self.category_anime["More Episodes"][0]
        self.series_sequel = anime(
            400,
            "Series Sequel",
            "2020-01-01",
            self.series_representative["series_episodes"],
        )
        self.side_story = anime(401, "Side Story", "2015-01-01", 3)
        all_anime = [
            entry
            for entries in self.category_anime.values()
            for entry in entries
        ] + [self.series_sequel, self.side_story]
        upsert_anime_records(all_anime, self.database_path)

        for entry in self.category_anime["More Episodes"]:
            store_anime_relations(entry["mal_id"], [], self.database_path)
        store_anime_relations(
            self.series_representative["mal_id"],
            [
                {"mal_id": 400, "relation_type": "sequel"},
                {"mal_id": 401, "relation_type": "side_story"},
            ],
            self.database_path,
        )
        store_anime_relations(
            400,
            [{"mal_id": self.series_representative["mal_id"], "relation_type": "prequel"}],
            self.database_path,
        )
        store_anime_relations(
            401,
            [{"mal_id": self.series_representative["mal_id"], "relation_type": "parent_story"}],
            self.database_path,
        )

    def valid_challenge(self):
        return [
            {**rule, "anime": list(self.category_anime[rule["name"]])}
            for rule in CATEGORY_RULES
        ]

    def store_recent_placement(
        self,
        category,
        mal_id,
        challenge_date="2026-09-22",
    ):
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "INSERT INTO challenge_runs VALUES (1, ?, 'now', 'approved')",
                (challenge_date,),
            )
            connection.execute(
                "INSERT INTO challenge_anime VALUES (1, ?, 1, ?)",
                (category, mal_id),
            )
            connection.commit()
        finally:
            connection.close()

    def test_public_validation_rejects_sequel_and_derivative_child(self):
        validate_public_challenge(
            self.valid_challenge(), "2026-09-23", self.database_path
        )

        sequel_challenge = self.valid_challenge()
        sequel_challenge[2]["anime"][0] = self.series_sequel
        with self.assertRaisesRegex(
            PublicChallengeValidationError,
            "not its canonical series representative",
        ):
            validate_public_challenge(
                sequel_challenge, "2026-09-23", self.database_path
            )

        side_story_challenge = self.valid_challenge()
        side_story_challenge[2]["anime"][0] = self.side_story
        with self.assertRaisesRegex(
            PublicChallengeValidationError,
            "no verified series representative",
        ):
            validate_public_challenge(
                side_story_challenge, "2026-09-23", self.database_path
            )

        missing_total_challenge = self.valid_challenge()
        missing_total_challenge[2]["anime"][0] = {
            **self.series_representative,
            "series_episodes": None,
        }
        with self.assertRaisesRegex(
            PublicChallengeValidationError,
            "missing series_episodes value",
        ):
            validate_public_challenge(
                missing_total_challenge, "2026-09-23", self.database_path
            )

    def test_recent_usage_is_category_specific_and_normalizes_series(self):
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "INSERT INTO challenge_runs VALUES (1, '2026-09-22', 'now', 'approved')"
            )
            connection.executemany(
                "INSERT INTO challenge_anime VALUES (?, ?, ?, ?)",
                (
                    (1, "Higher Score", 1, self.category_anime["Higher Score"][0]["mal_id"]),
                    (1, "More Episodes", 1, self.series_sequel["mal_id"]),
                ),
            )
            connection.commit()
        finally:
            connection.close()

        recent = load_recent_category_usage("2026-09-23", self.database_path)
        score_anime = self.category_anime["Higher Score"][0]
        score_rule = CATEGORY_RULES[0]
        popularity_rule = CATEGORY_RULES[1]
        self.assertFalse(
            is_eligible(score_anime, score_rule, set(), recent["Higher Score"])
        )
        self.assertTrue(
            is_eligible(score_anime, popularity_rule, set(), recent.get("More Popular", set()))
        )
        self.assertEqual(
            recent["More Episodes"], {self.series_representative["mal_id"]}
        )
        episodes_rule = CATEGORY_RULES[2]
        self.assertFalse(
            is_eligible(
                self.series_representative,
                episodes_rule,
                set(),
                recent["More Episodes"],
                {self.series_representative["mal_id"]},
            )
        )

    def test_generation_never_retries_without_category_cooldown(self):
        catalog = [
            entry
            for entries in self.category_anime.values()
            for entry in entries
        ]
        recent = {"Higher Score": {catalog[0]["mal_id"]}}
        with patch("challenge.try_generate_challenge", return_value=None) as attempt:
            with self.assertRaisesRegex(RuntimeError, "category-specific"):
                generate_challenge(
                    catalog,
                    random.Random(1),
                    recent_anime_ids_by_category=recent,
                )
        self.assertEqual(attempt.call_count, 1)
        self.assertIs(attempt.call_args.args[1], recent)

    def test_postgres_hybrid_rows_are_read_by_column_name(self):
        class Cursor:
            def fetchall(self):
                return [
                    database_module._HybridRow(
                        category="Higher Score",
                        mal_id=123,
                    ),
                    database_module._HybridRow(
                        category="More Episodes",
                        mal_id=456,
                    ),
                ]

        class Connection:
            def execute(self, sql, parameters):
                return Cursor()

            def close(self):
                pass

        with (
            patch("database.initialize_database"),
            patch("database._connect_database", return_value=Connection()),
        ):
            recent = load_recent_category_anime_ids(
                "2026-09-23",
                7,
                self.database_path,
            )

        self.assertEqual(
            recent,
            {
                "Higher Score": {123},
                "More Episodes": {456},
            },
        )

    def test_public_validation_rejects_same_category_cooldown_reuse(self):
        repeated = self.category_anime["Higher Score"][0]
        self.store_recent_placement("Higher Score", repeated["mal_id"])

        with self.assertRaisesRegex(
            PublicChallengeValidationError,
            "repeats Higher Score within the 7-day category cooldown",
        ):
            validate_public_challenge(
                self.valid_challenge(), "2026-09-23", self.database_path
            )

    def test_public_validation_normalizes_more_episodes_cooldown(self):
        self.store_recent_placement("More Episodes", self.series_sequel["mal_id"])

        with self.assertRaisesRegex(
            PublicChallengeValidationError,
            "repeats More Episodes within the 7-day category cooldown",
        ):
            validate_public_challenge(
                self.valid_challenge(), "2026-09-23", self.database_path
            )

    def test_public_validation_allows_cross_category_history_reuse(self):
        reused = self.category_anime["Higher Score"][0]
        self.store_recent_placement("More Popular", reused["mal_id"])

        validate_public_challenge(
            self.valid_challenge(), "2026-09-23", self.database_path
        )

    def test_public_validation_does_not_compare_challenge_to_itself(self):
        repeated = self.category_anime["Higher Score"][0]
        self.store_recent_placement(
            "Higher Score",
            repeated["mal_id"],
            challenge_date="2026-09-23",
        )

        validate_public_challenge(
            self.valid_challenge(), "2026-09-23", self.database_path
        )


if __name__ == "__main__":
    unittest.main()
