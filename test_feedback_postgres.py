"""Opt-in real PostgreSQL checks in a disposable, password-protected local cluster.

Set ANIME_DAILY_TEST_POSTGRES_BIN to an installed PostgreSQL bin directory.
Never uses an existing DATABASE_URL, server, database, or cluster directory.
"""

import io
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from uuid import uuid4

import database as db
from feedback import FeedbackSubmission
from manage_feedback import main as feedback_cli


@unittest.skipUnless(os.getenv("ANIME_DAILY_TEST_POSTGRES_BIN"), "Set an installed local PostgreSQL bin path for isolated engine tests")
class FeedbackPostgresTests(unittest.TestCase):
    def test_feedback_migration_queries_and_preservation(self):
        self.assertIsNotNone(db.psycopg, "psycopg is required; never fall back to the real SQLite database")
        binaries = Path(os.environ["ANIME_DAILY_TEST_POSTGRES_BIN"])
        suffix = ".exe" if os.name == "nt" else ""
        initdb = binaries / f"initdb{suffix}"
        control = binaries / f"pg_ctl{suffix}"
        self.assertTrue(initdb.is_file() and control.is_file())
        password = secrets.token_hex(24)
        with tempfile.TemporaryDirectory(prefix="animoredle-postgres-test-") as temporary:
            root = Path(temporary)
            cluster = root / "cluster"
            password_file = root / "password"
            password_file.write_text(password, encoding="utf-8")
            with socket.socket() as port_probe:
                port_probe.bind(("127.0.0.1", 0))
                port = port_probe.getsockname()[1]

            def run(*arguments):
                # PostgreSQL children can inherit pipes on Windows and keep a
                # capture_output communicate() waiting after pg_ctl has exited.
                with (root / "control.log").open("a", encoding="utf-8") as output:
                    result = subprocess.run(
                        [str(argument) for argument in arguments], stdout=output,
                        stderr=subprocess.STDOUT, timeout=60,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    )
                if result.returncode:
                    detail = (root / "control.log").read_text(encoding="utf-8", errors="replace")[-1500:].replace(password, "[redacted]")
                    raise RuntimeError(f"Isolated PostgreSQL command failed: {detail}")

            run(initdb, "-D", cluster, "-U", "postgres", "-A", "scram-sha-256",
                "--pwfile", password_file, "--encoding=UTF8", "--no-locale")
            url = f"postgresql://postgres:{password}@127.0.0.1:{port}/postgres?connect_timeout=5"
            try:
                run(control, "-D", cluster, "-l", root / "server.log", "-o",
                    f"-h 127.0.0.1 -p {port}", "-w", "-t", "30", "start")
                with patch.dict(os.environ, {"DATABASE_URL": url}):
                    try:
                        self.check_feedback_operations()
                    finally:
                        db._POSTGRES_INITIALIZED_TARGETS.discard(("postgres", url))
            finally:
                if (cluster / "postmaster.pid").exists():
                    run(control, "-D", cluster, "-m", "fast", "-w", "-t", "30", "stop")

    def check_feedback_operations(self):
        self.assertTrue(db._postgres_enabled(db.DATABASE_PATH))
        db.initialize_database()
        db.upsert_anime_records([
            {"mal_id": 100, "title": "Local PostgreSQL fixture A", "type": "tv"},
            {"mal_id": 101, "title": "Local PostgreSQL fixture B", "type": "tv"},
        ])
        db.ensure_player("local-postgres-player")
        connection = db._connect_database(db.DATABASE_PATH)
        try:
            connection.execute("INSERT INTO anime_relations VALUES (100, 101, 'sequel')")
            connection.execute("INSERT INTO catalog_ingestion_failures VALUES (102, 'Local failure', 'details', 'Test only', '2026-01-01')")
            connection.execute("INSERT INTO ingestion_state VALUES ('local-test', 'preserved')")
            challenge_id = connection.execute(
                "INSERT INTO challenge_runs (challenge_date, created_at) VALUES ('2026-09-20', '2026-09-20') RETURNING id"
            ).fetchone()[0]
            for position, mal_id in enumerate((100, 101), 1):
                connection.execute("INSERT INTO challenge_anime VALUES (?, 'Higher Score', ?, ?)",
                                   (challenge_id, position, mal_id))
            connection.execute("INSERT INTO matchup_history VALUES (?, 'Higher Score', 100, 101, '2026-09-20')", (challenge_id,))
            connection.execute("DROP TABLE feedback_submissions")
            connection.commit()
        finally:
            connection.close()
        db.record_player_answer("local-postgres-player", challenge_id, "Higher Score", 1, 101, 101, True)
        db.record_player_result("local-postgres-player", challenge_id, 1)
        tables = ("anime", "anime_relations", "catalog_ingestion_failures", "ingestion_state",
                  "challenge_runs", "challenge_anime", "matchup_history", "players",
                  "player_answers", "player_results")

        def snapshot():
            connection = db._connect_database(db.DATABASE_PATH)
            try:
                return {table: connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                        for table in tables}
            finally:
                connection.close()

        before = snapshot()
        # Force actual schema SQL twice, not just the successful-initialization cache.
        for _ in range(2):
            db._POSTGRES_INITIALIZED_TARGETS.discard(db._series_representative_cache_key(db.DATABASE_PATH))
            db.initialize_database()
        connection = db._connect_database(db.DATABASE_PATH)
        try:
            self.assertIsNotNone(connection.execute("SELECT to_regclass('feedback_submissions')").fetchone()[0])
            self.assertIsNotNone(connection.execute("SELECT to_regclass('feedback_created_index')").fetchone()[0])
        finally:
            connection.close()
        report = FeedbackSubmission(id=str(uuid4()), kind="bug", message="Local PostgreSQL feedback test.",
                                    email="test@example.org", category="Higher Score").model_dump(mode="json", exclude={"website"})
        self.assertTrue(db.save_feedback_submission(report, "same-content"))
        self.assertFalse(db.save_feedback_submission(report, "same-content"))
        with self.assertRaises(ValueError):
            db.save_feedback_submission({**report, "message": "Changed content"}, "different-content")
        general = FeedbackSubmission(id=str(uuid4()), kind="feedback", message="A local PostgreSQL suggestion.",
                                     category="Other").model_dump(mode="json", exclude={"website"})
        self.assertTrue(db.save_feedback_submission(general, "general-content"))
        self.assertEqual(len(db.load_feedback_submissions()), 2)
        self.assertEqual(db.load_feedback_submissions(kind="bug")[0]["message"], report["message"])
        self.assertEqual(db.load_feedback_submissions(kind="feedback")[0]["id"], general["id"])
        self.assertEqual(db.load_feedback_submissions(submission_id=report["id"])[0]["email"], "test@example.org")
        for arguments in (["list"], ["show", report["id"]], ["prune"]):
            with redirect_stdout(io.StringIO()) as output:
                feedback_cli(arguments)
            self.assertTrue(output.getvalue())
        connection = db._connect_database(db.DATABASE_PATH)
        try:
            connection.execute("UPDATE feedback_submissions SET created_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (report["id"],))
            connection.commit()
        finally:
            connection.close()
        self.assertEqual(db.prune_feedback_submissions("1900-01-01"), 0)
        self.assertEqual(db.prune_feedback_submissions("2020-01-01"), 1)
        self.assertEqual(len(db.load_feedback_submissions()), 2)
        with redirect_stdout(io.StringIO()) as output:
            feedback_cli(["prune", "--apply"])
        self.assertIn("Deleted 1", output.getvalue())
        self.assertEqual(len(db.load_feedback_submissions()), 1)
        self.assertEqual(db.prune_feedback_submissions("2999-01-01", apply=True), 1)
        self.assertEqual(db.load_feedback_submissions(), [])
        self.assertEqual(before, snapshot())


if __name__ == "__main__":
    unittest.main()
