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
from database import DATABASE_PATH


class ArchiveRouteTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory(
            ignore_cleanup_errors=True
        )
        self.database_path = Path(self.temporary_directory.name) / "game.db"
        shutil.copyfile(DATABASE_PATH, self.database_path)
        self.original_database_path = app_module.app.state.database_path
        app_module.app.state.database_path = self.database_path
        self.client = TestClient(app_module.app)

    def tearDown(self):
        self.client.close()
        app_module.app.state.database_path = self.original_database_path
        self.temporary_directory.cleanup()

    def test_historical_route_loads_progress_and_requires_exact_date(self):
        response = self.client.get("/challenge/2026-09-16")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["challenge_date"], "2026-09-16")
        self.assertIn("progress", payload)

        first_category = payload["categories"][0]
        answer = self.client.post(
            "/challenge/2026-09-16/answer",
            json={
                "category": first_category["name"],
                "comparison_position": 1,
                "selected_mal_id": first_category["anime"][0]["mal_id"],
            },
        )
        self.assertEqual(answer.status_code, 200)
        resumed = self.client.get("/challenge/2026-09-16").json()["progress"]
        self.assertEqual(len(resumed["answers"]), 1)
        self.assertEqual(
            resumed["next"],
            {"category": first_category["name"], "comparison_position": 2},
        )
        self.assertEqual(
            self.client.get("/challenge/today?local_date=2026-09-16").status_code,
            200,
        )

        self.assertEqual(
            self.client.get("/challenge/2026-09-17").status_code,
            404,
        )

        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                "UPDATE challenge_runs SET publication_state = 'draft' "
                "WHERE challenge_date = '2026-09-16'"
            )

        self.assertEqual(
            self.client.get("/challenge/2026-09-16").status_code,
            404,
        )
        self.assertEqual(
            self.client.get("/challenge/today?local_date=2026-09-16").status_code,
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
                app_module.load_challenge_for_api(date(2026, 9, 20))
        self.assertEqual(context.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
