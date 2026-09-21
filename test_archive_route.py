"""Focused regression checks for dated official challenge loading."""

import shutil
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

import app as app_module
from challenge import load_stored_challenge
from database import DATABASE_PATH, record_challenge


class ArchiveRouteTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory(
            ignore_cleanup_errors=True
        )
        self.database_path = Path(self.temporary_directory.name) / "game.db"
        shutil.copyfile(DATABASE_PATH, self.database_path)
        source_challenge = load_stored_challenge(
            "2026-09-16", self.database_path
        )
        record_challenge(source_challenge, "2026-09-19", self.database_path)
        record_challenge(source_challenge, "2026-09-20", self.database_path)
        self.original_database_path = app_module.app.state.database_path
        app_module.app.state.database_path = self.database_path
        self.client = TestClient(app_module.app)

    def tearDown(self):
        self.client.close()
        app_module.app.state.database_path = self.original_database_path
        self.temporary_directory.cleanup()

    def test_historical_route_loads_progress_and_requires_exact_date(self):
        response = self.client.get("/challenge/2026-09-19")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["challenge_date"], "2026-09-19")
        self.assertEqual(payload["public_history_start"], "2026-09-19")
        self.assertIn("progress", payload)

        first_category = payload["categories"][0]
        answer = self.client.post(
            "/challenge/2026-09-19/answer",
            json={
                "category": first_category["name"],
                "comparison_position": 1,
                "selected_mal_id": first_category["anime"][0]["mal_id"],
            },
        )
        self.assertEqual(answer.status_code, 200)
        resumed = self.client.get("/challenge/2026-09-19").json()["progress"]
        self.assertEqual(len(resumed["answers"]), 1)
        self.assertEqual(
            resumed["next"],
            {"category": first_category["name"], "comparison_position": 2},
        )
        self.assertEqual(
            self.client.get("/challenge/today?local_date=2026-09-20").status_code,
            200,
        )

        self.assertEqual(
            self.client.get("/challenge/2026-09-18").status_code,
            404,
        )
        self.assertEqual(
            self.client.get("/challenge/today?local_date=2026-09-18").status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                "/challenge/2026-09-18/answer",
                json={
                    "category": first_category["name"],
                    "comparison_position": 1,
                    "selected_mal_id": first_category["anime"][0]["mal_id"],
                },
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                "/challenge/2026-09-18/complete",
                json={"answers": []},
            ).status_code,
            404,
        )

        archive = self.client.get(
            "/archive?year=2026&month=9&local_date=2026-09-21"
        ).json()
        archive_dates = [entry["challenge_date"] for entry in archive["challenges"]]
        self.assertEqual(archive["public_history_start"], "2026-09-19")
        self.assertIn("2026-09-19", archive_dates)
        self.assertIn("2026-09-20", archive_dates)
        self.assertNotIn("2026-09-18", archive_dates)
        self.assertFalse(any(value < "2026-09-19" for value in archive_dates))

        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                "UPDATE challenge_runs SET publication_state = 'draft' "
                "WHERE challenge_date = '2026-09-20'"
            )

        self.assertEqual(
            self.client.get("/challenge/2026-09-20").status_code,
            404,
        )
        self.assertEqual(
            self.client.get("/challenge/2026-09-19").status_code,
            200,
        )
        self.assertEqual(
            self.client.get("/challenge/today?local_date=2026-09-20").status_code,
            404,
        )

    def test_synthetic_title_guard_explains_unavailable_response(self):
        synthetic = [
            {
                "name": "More Episodes",
                "anime": [{"title": "More Episodes3"}],
            }
        ]
        with patch.object(app_module, "load_stored_challenge", return_value=synthetic):
            with self.assertRaises(HTTPException) as context:
                app_module.load_challenge_for_api(date(2026, 9, 19))
        self.assertEqual(context.exception.status_code, 503)

    def test_internal_history_remains_available_before_public_cutoff(self):
        challenge = load_stored_challenge("2026-09-16", self.database_path)
        self.assertIsNotNone(challenge)
        self.assertEqual(len(challenge), 4)


if __name__ == "__main__":
    unittest.main()
