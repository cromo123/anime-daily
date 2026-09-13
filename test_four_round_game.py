"""Regression checks for the four-round challenge and legacy archive."""

import sqlite3
import tempfile
import unittest
import random
from contextlib import closing
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import DEV_MODE, app
from challenge import (
    ANIME_PER_CATEGORY,
    CATEGORY_RULES,
    TOTAL_QUESTIONS,
    evaluate_comparison,
    generate_history_aware_challenge,
    get_comparison_value,
    has_eligible_display_type,
    verify_completed_answers,
)
from challenge_curator import CuratorDecision
from challenge_ratings import build_rating_context, rate_challenge
from daily_challenge_flow import DailyChallengeFlow
from database import (
    DATABASE_PATH,
    load_anime_records,
    load_player_results,
    record_challenge,
)
from editorial_planner import EditorialBrief


TABLES = (
    "challenge_runs",
    "challenge_anime",
    "matchup_history",
    "players",
    "player_results",
)


def row_counts(path):
    with closing(sqlite3.connect(path)) as connection:
        return {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in TABLES
        }


class FourRoundGameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary_directory = tempfile.TemporaryDirectory()
        cls.database_path = Path(cls.temporary_directory.name) / "game.db"
        with closing(sqlite3.connect(DATABASE_PATH)) as source:
            with closing(sqlite3.connect(cls.database_path)) as target:
                source.backup(target)
        cls.original_app_path = app.state.database_path
        app.state.database_path = cls.database_path

    @classmethod
    def tearDownClass(cls):
        app.state.database_path = cls.original_app_path
        cls.temporary_directory.cleanup()

    def test_two_complete_challenges_and_api_results(self):
        self.assertEqual(len(CATEGORY_RULES), 4)
        self.assertEqual(TOTAL_QUESTIONS, 20)
        for category in CATEGORY_RULES:
            self.assertEqual(
                has_eligible_display_type({"type": "movie"}, category),
                category["name"] != "More Episodes",
            )
        rating_context = build_rating_context(load_anime_records(self.database_path))

        with TestClient(app) as client:
            frontend = client.get("/")
            self.assertEqual(frontend.status_code, 200)
            self.assertIn("Round 1 / 4", frontend.text)
            self.assertIn("0 / 20", frontend.text)
            self.assertIn('class="promo-slot"', frontend.text)
            game_script = client.get("/static/game.js").text
            self.assertIn("const TOTAL_ROUNDS = 4;", game_script)
            self.assertIn("Full anime series", game_script)
            self.assertNotIn("Longer Runtime", game_script)
            for challenge_date in ("2026-09-11", "2026-09-12"):
                challenge = generate_history_aware_challenge(
                    challenge_date, self.database_path
                )
                self.assertEqual(
                    [category["name"] for category in challenge],
                    [rule["name"] for rule in CATEGORY_RULES],
                )
                ids = [
                    anime["mal_id"]
                    for category in challenge
                    for anime in category["anime"]
                ]
                self.assertEqual(len(ids), 24)
                self.assertEqual(len(set(ids)), 24)
                answers = []
                for category in challenge:
                    self.assertEqual(len(category["anime"]), ANIME_PER_CATEGORY)
                    for index in range(5):
                        first, second = category["anime"][index:index + 2]
                        metric = category["metric"]
                        value_a = get_comparison_value(first, metric)
                        value_b = get_comparison_value(second, metric)
                        self.assertIsNotNone(value_a)
                        self.assertIsNotNone(value_b)
                        self.assertNotEqual(value_a, value_b)
                        selected = first if value_a > value_b else second
                        answers.append({
                            "category": category["name"],
                            "comparison_position": index + 1,
                            "selected_mal_id": selected["mal_id"],
                        })
                        self.assertTrue(evaluate_comparison(
                            challenge, category["name"], index + 1,
                            selected["mal_id"],
                        )["correct"])
                    if category["name"] == "More Episodes":
                        self.assertTrue(all(
                            anime["type"] in {"tv", "ona", "ova", "special", "tv_special"}
                            for anime in category["anime"]
                        ))
                        self.assertEqual(metric, "series_episodes")

                self.assertEqual(len(answers), 20)
                ratings = rate_challenge(challenge, rating_context=rating_context)
                self.assertEqual(len(ratings["categories"]), 4)
                self.assertEqual(sum(
                    len(category["comparisons"]) for category in ratings["categories"]
                ), 20)
                self.assertEqual(verify_completed_answers(challenge, answers), 20)
                with self.assertRaisesRegex(ValueError, "20 answers"):
                    verify_completed_answers(challenge, answers + [answers[0]])
                record_challenge(challenge, challenge_date, self.database_path)
                public = client.get(f"/challenge/{challenge_date}")
                self.assertEqual(public.status_code, 200)
                self.assertEqual(len(public.json()["categories"]), 4)
                self.assertEqual(sum(
                    len(category["anime"]) for category in public.json()["categories"]
                ), 24)
                for answer in answers:
                    response = client.post(
                        f"/challenge/{challenge_date}/answer", json=answer
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(response.json()["correct"])
                completion = client.post(
                    f"/challenge/{challenge_date}/complete", json={"answers": answers}
                )
                self.assertEqual(completion.status_code, 200)
                self.assertEqual(completion.json()["verified_score"], 20)
                self.assertEqual(completion.json()["total_questions"], 20)
                self.assertEqual(completion.json()["percentage"], 100)
                wrong_answers = []
                for category_index, category in enumerate(challenge):
                    for position in range(1, 6):
                        correct_id = answers[
                            category_index * 5 + position - 1
                        ]["selected_mal_id"]
                        other_id = next(
                            anime["mal_id"]
                            for anime in category["anime"][position - 1:position + 1]
                            if anime["mal_id"] != correct_id
                        )
                        wrong_answers.append({
                            "category": category["name"],
                            "comparison_position": position,
                            "selected_mal_id": other_id,
                        })
                replay = client.post(
                    f"/challenge/{challenge_date}/complete",
                    json={"answers": wrong_answers},
                )
                self.assertEqual(replay.status_code, 200)
                self.assertEqual(replay.json()["verified_score"], 0)
                self.assertEqual(replay.json()["official_score"], 20)
                self.assertTrue(replay.json()["replay"])
                self.assertEqual(client.post(
                    f"/challenge/{challenge_date}/complete",
                    json={"answers": answers + [answers[0]]},
                ).status_code, 400)

            history = client.get("/player/history").json()["results"]
            self.assertEqual(len(history), 2)
            self.assertTrue(all(result["total_questions"] == 20 for result in history))
            archive = client.get("/archive?year=2026&month=9").json()["challenges"]
            recent = [entry for entry in archive if entry["challenge_date"] in {
                "2026-09-11", "2026-09-12"
            }]
            self.assertEqual(len(recent), 2)
            self.assertTrue(all(entry["playable"] for entry in recent))
            legacy = next(entry for entry in archive if entry["challenge_date"] == "2026-09-03")
            self.assertEqual(legacy["total_questions"], 25)
            self.assertFalse(legacy["playable"])
            self.assertEqual(client.get("/challenge/2026-09-03").status_code, 409)

            with closing(sqlite3.connect(self.database_path)) as connection:
                old_player = connection.execute(
                    "SELECT player_id FROM player_results LIMIT 1"
                ).fetchone()[0]
            old_result = load_player_results(old_player, self.database_path)[0]
            self.assertEqual(old_result["score"], 12)
            self.assertEqual(old_result["total_questions"], 25)

    def test_playtest_flow_curates_without_persistence(self):
        before = row_counts(DATABASE_PATH)
        brief = EditorialBrief(
            target_popularity=92.5,
            target_difficulty=77.5,
            target_modernity=68,
            wildcard_target=1,
            notes=["Varied eras"],
        )
        def select_first(shortlist, *_args, **_kwargs):
            return CuratorDecision(
                candidate_id=shortlist[0]["candidate_id"],
                reason_tags=["variety"],
                summary="Valid selection",
            )

        with patch("daily_challenge_flow.run_editorial_planner", return_value=brief) as planner:
            with patch("daily_challenge_flow.run_challenge_curator", side_effect=select_first) as curator:
                flow = DailyChallengeFlow.for_playtest(date.today().isoformat())
                selected = flow.kickoff()
        planner.assert_called_once()
        curator.assert_called_once()
        self.assertEqual(len(flow.state.shortlisted_candidates), 20)
        self.assertEqual(len(selected), 4)
        self.assertEqual(flow.state.selected_candidate["categories"], selected)
        self.assertEqual(row_counts(DATABASE_PATH), before)

    @unittest.skipUnless(DEV_MODE, "Enable ANIME_DAILY_DEV_MODE for playtest API")
    def test_two_playtests_finish_without_any_history_or_player_writes(self):
        before = row_counts(self.database_path)
        challenges = [
            generate_history_aware_challenge(
                "2026-09-13", self.database_path, random.Random(seed)
            )
            for seed in (101, 102)
        ]
        self.assertNotEqual(
            [[anime["mal_id"] for anime in category["anime"]] for category in challenges[0]],
            [[anime["mal_id"] for anime in category["anime"]] for category in challenges[1]],
        )

        def fake_flow(challenge):
            return SimpleNamespace(
                kickoff=lambda: challenge,
                state=SimpleNamespace(
                    selected_candidate={"categories": challenge},
                    challenge_date="2026-09-13",
                ),
            )

        with patch(
            "daily_challenge_flow.DailyChallengeFlow.for_playtest",
            side_effect=[fake_flow(challenge) for challenge in challenges],
        ) as pipeline:
            with TestClient(app) as client:
                for expected in challenges:
                    response = client.post("/dev/playtest/generate")
                    self.assertEqual(response.status_code, 200)
                    public = response.json()
                    self.assertEqual(len(public["categories"]), 4)
                    self.assertEqual(client.get("/dev/playtest").json(), public)
                    score = 0
                    comparisons = 0
                    for category in public["categories"]:
                        for position in range(1, 6):
                            selected_id = category["anime"][position - 1]["mal_id"]
                            answer = client.post(
                                f"/dev/playtest/{public['playtest_id']}/answer",
                                json={
                                    "category": category["name"],
                                    "comparison_position": position,
                                    "selected_mal_id": selected_id,
                                },
                            )
                            self.assertEqual(answer.status_code, 200)
                            score += answer.json()["correct"]
                            comparisons += 1
                    self.assertEqual(comparisons, 20)
                    self.assertEqual(
                        score,
                        verify_completed_answers(expected, [
                            {
                                "category": category["name"],
                                "comparison_position": position,
                                "selected_mal_id": category["anime"][position - 1]["mal_id"],
                            }
                            for category in public["categories"]
                            for position in range(1, 6)
                        ]),
                    )
        self.assertEqual(pipeline.call_count, 2)
        self.assertEqual(row_counts(self.database_path), before)


if __name__ == "__main__":
    unittest.main()
