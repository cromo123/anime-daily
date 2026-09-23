import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import database
import manage_challenges


class ReplenishTests(unittest.TestCase):
    def test_approved_replenish_uses_fast_path(self):
        with (
            patch.object(
                manage_challenges,
                "load_challenge_record",
                return_value={"publication_state": "approved"},
            ),
            patch.object(manage_challenges, "_validate_stored_date") as validate,
            patch("builtins.print"),
        ):
            manage_challenges.replenish(0, date(2026, 9, 23))

        validate.assert_not_called()

    def test_draft_replenish_still_validates_before_approval(self):
        with (
            patch.object(
                manage_challenges,
                "load_challenge_record",
                return_value={"publication_state": "draft"},
            ),
            patch.object(
                manage_challenges,
                "_validate_stored_date",
                return_value=[],
            ) as validate,
            patch.object(
                manage_challenges,
                "set_challenge_publication_state",
                return_value=True,
            ) as approve,
            patch("builtins.print"),
        ):
            manage_challenges.replenish(0, date(2026, 9, 23))

        validate.assert_called_once()
        approve.assert_called_once()

    def test_missing_replenish_generates_and_validates(self):
        with (
            patch.object(
                manage_challenges,
                "load_challenge_record",
                return_value=None,
            ),
            patch.object(
                manage_challenges,
                "_generate_and_approve",
                return_value=1,
            ) as generate,
            patch("builtins.print"),
        ):
            manage_challenges.replenish(0, date(2026, 9, 23))

        generate.assert_called_once_with(date(2026, 9, 23))

    def test_explicit_validation_still_calls_public_validator(self):
        with (
            patch.object(
                manage_challenges,
                "load_stored_challenge",
                return_value=[],
            ),
            patch.object(
                manage_challenges,
                "validate_public_challenge",
            ) as validate,
        ):
            manage_challenges._validate_stored_date(date(2026, 9, 23))

        validate.assert_called_once()


class DatabaseInitializationTests(unittest.TestCase):
    class FakeConnection:
        is_postgres = True

        def __init__(self):
            self.commits = 0
            self.rollbacks = 0
            self.closed = 0

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

        def close(self):
            self.closed += 1

    def setUp(self):
        database._POSTGRES_INITIALIZED_TARGETS.clear()

    def test_postgres_initialization_is_cached_after_success(self):
        connection = self.FakeConnection()
        with (
            patch.dict(
                os.environ,
                {"DATABASE_URL": "postgresql://test.example/anime"},
            ),
            patch.object(database, "_postgres_enabled", return_value=True),
            patch.object(database, "_connect_database", return_value=connection),
            patch.object(database, "_initialize_postgres") as initialize,
        ):
            database.initialize_database(Path("temporary.db"))
            database.initialize_database(Path("temporary.db"))

        initialize.assert_called_once()
        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.closed, 1)

    def test_failed_postgres_initialization_is_retryable(self):
        connection = self.FakeConnection()
        initialize = patch.object(
            database,
            "_initialize_postgres",
            side_effect=[RuntimeError("temporary failure"), None],
        )
        with patch.dict(
            os.environ,
            {"DATABASE_URL": "postgresql://test.example/anime"},
        ), patch.object(
            database, "_postgres_enabled", return_value=True
        ), patch.object(
            database, "_connect_database", return_value=connection
        ), initialize as initialize_mock:
            with self.assertRaisesRegex(RuntimeError, "temporary failure"):
                database.initialize_database(Path("temporary.db"))
            database.initialize_database(Path("temporary.db"))

        self.assertEqual(initialize_mock.call_count, 2)
        self.assertEqual(connection.rollbacks, 1)
        self.assertEqual(connection.commits, 1)

    def test_temp_sqlite_initialization_remains_isolated(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "anime_daily.db"
            database.initialize_database(path)
            database.initialize_database(path)
            self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
