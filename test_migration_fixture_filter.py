import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from database import _connect_database, initialize_database
from fixture_safety import KNOWN_SYNTHETIC_MAL_IDS
from migrate_sqlite_to_postgres import (
    TABLES_IN_ORDER,
    _copy_table,
    _destination_is_empty,
    _print_preflight_report,
    _scan_source,
    _verify_migration,
)


FIXTURE_GROUPS = (
    (991000, "Higher Score"),
    (991010, "More Popular"),
    (991020, "More Episodes"),
    (991030, "More Recent"),
)


class MigrationFixtureFilterTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory(
            ignore_cleanup_errors=True
        )
        root = Path(self.temporary_directory.name)
        self.source_path = root / "source.db"
        self.destination_path = root / "destination.db"
        initialize_database(self.source_path)
        initialize_database(self.destination_path)
        self.source = _connect_database(self.source_path)
        self.destination = _connect_database(self.destination_path)
        self._seed_source()

    def tearDown(self):
        self.source.close()
        self.destination.close()
        self.temporary_directory.cleanup()

    def _insert_anime(self, mal_id, title):
        self.source.execute(
            """
            INSERT INTO anime (
                mal_id, title, image_url, score, popularity_rank, members,
                entry_episodes, series_episodes, release_date, type,
                runtime_minutes, relations_fetched
            ) VALUES (?, ?, NULL, 8.0, 100, 1000, 12, 12, '2026-01-01',
                      'tv', 24, 1)
            """,
            (mal_id, title),
        )

    def _seed_source(self):
        self._insert_anime(100, "Legitimate Anime A")
        self._insert_anime(101, "Legitimate Anime B")
        for first_id, title_prefix in FIXTURE_GROUPS:
            for offset in range(6):
                self._insert_anime(first_id + offset, f"{title_prefix}{offset}")

        self.source.executemany(
            "INSERT INTO anime_relations VALUES (?, ?, ?)",
            (
                (100, 101, "sequel"),
                (991000, 100, "sequel"),
                (100, 991001, "prequel"),
            ),
        )
        self.source.executemany(
            "INSERT INTO catalog_ingestion_failures VALUES (?, ?, ?, ?, ?)",
            (
                (500, "Legitimate Failure", "detail", "temporary", "now"),
                (991002, "Higher Score2", "detail", "fixture", "now"),
            ),
        )
        self.source.execute(
            "INSERT INTO ingestion_state VALUES ('ranking_page', '2')"
        )
        self.source.executemany(
            "INSERT INTO challenge_runs VALUES (?, ?, ?, ?)",
            (
                (1, "2026-09-19", "now", "approved"),
                (2, "2099-01-01", "now", "approved"),
            ),
        )
        self.source.executemany(
            "INSERT INTO challenge_anime VALUES (?, ?, ?, ?)",
            (
                (1, "Higher Score", 1, 100),
                (1, "Higher Score", 2, 101),
                (2, "Higher Score", 1, 991000),
                (2, "Higher Score", 2, 100),
            ),
        )
        self.source.executemany(
            "INSERT INTO matchup_history VALUES (?, ?, ?, ?, ?)",
            (
                (1, "Higher Score", 100, 101, "2026-09-19"),
                (2, "Higher Score", 100, 991000, "2099-01-01"),
            ),
        )
        self.source.executemany(
            "INSERT INTO players VALUES (?, ?)",
            (("legitimate-player", "now"), ("fixture-player", "now")),
        )
        self.source.executemany(
            "INSERT INTO player_results VALUES (?, ?, ?, ?)",
            (
                ("legitimate-player", 1, 1, "now"),
                ("fixture-player", 2, 1, "now"),
            ),
        )
        self.source.executemany(
            "INSERT INTO player_answers VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ("legitimate-player", 1, "Higher Score", 1, 100, 100, 1, "now"),
                ("fixture-player", 2, "Higher Score", 1, 991000, 991000, 1, "now"),
            ),
        )
        self.source.commit()

    def test_filters_known_fixtures_and_preserves_legitimate_data(self):
        plan = _scan_source(self.source)
        self.assertEqual(plan["fixture_anime_count"], 24)
        self.assertEqual(
            plan["contaminated_challenges"],
            [{"id": 2, "challenge_date": "2099-01-01"}],
        )
        self.assertEqual(plan["skipped_counts"]["anime"], 24)
        self.assertEqual(plan["skipped_counts"]["anime_relations"], 2)
        self.assertEqual(plan["skipped_counts"]["catalog_ingestion_failures"], 1)
        self.assertEqual(plan["skipped_counts"]["challenge_runs"], 1)
        self.assertEqual(plan["skipped_counts"]["challenge_anime"], 2)
        self.assertEqual(plan["skipped_counts"]["matchup_history"], 1)
        self.assertEqual(plan["skipped_counts"]["player_answers"], 1)
        self.assertEqual(plan["skipped_counts"]["player_results"], 1)
        output = io.StringIO()
        with redirect_stdout(output):
            _print_preflight_report(plan)
        self.assertIn("Known fixture anime found: 24", output.getvalue())
        self.assertIn("#2 (2099-01-01)", output.getvalue())
        self.assertIn("player_answers=1", output.getvalue())

        counts = {
            table: _copy_table(self.source, self.destination, table, plan)
            for table in TABLES_IN_ORDER
        }
        _verify_migration(self.source, self.destination, counts, plan)
        self.destination.commit()

        imported_ids = {
            row[0] for row in self.destination.execute("SELECT mal_id FROM anime")
        }
        self.assertTrue(imported_ids.isdisjoint(KNOWN_SYNTHETIC_MAL_IDS))
        self.assertEqual(imported_ids, {100, 101})
        self.assertEqual(
            self.destination.execute(
                "SELECT challenge_date FROM challenge_runs"
            ).fetchall(),
            [("2026-09-19",)],
        )
        self.assertEqual(
            self.destination.execute("SELECT player_id FROM player_results").fetchall(),
            [("legitimate-player",)],
        )
        self.assertEqual(
            self.destination.execute("SELECT player_id FROM player_answers").fetchall(),
            [("legitimate-player",)],
        )
        self.assertEqual(
            self.destination.execute("SELECT COUNT(*) FROM anime_relations").fetchone()[0],
            1,
        )
        self.assertEqual(
            self.destination.execute(
                "SELECT publication_state FROM challenge_runs"
            ).fetchone()[0],
            "approved",
        )
        self.assertEqual(
            self.destination.execute(
                "SELECT value FROM ingestion_state WHERE key = 'ranking_page'"
            ).fetchone()[0],
            "2",
        )
        with self.assertRaisesRegex(RuntimeError, "already contains application rows"):
            _destination_is_empty(self.destination)

    def test_unexpected_fixture_title_fails_before_copying(self):
        self._insert_anime(123456, "Higher Score0")
        self.source.commit()

        with self.assertRaisesRegex(RuntimeError, "manual inspection"):
            _scan_source(self.source)
        _destination_is_empty(self.destination)


if __name__ == "__main__":
    unittest.main()
