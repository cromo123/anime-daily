"""Public content, read-only recaps, and private feedback on temporary SQLite."""

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from xml.etree import ElementTree

from fastapi.testclient import TestClient
from starlette.requests import Request

import app as web
import database
from feedback import FeedbackRateLimiter, MAX_PER_PEER
from manage_feedback import main as feedback_cli
from public_pages import STATIC_PAGES, historical_cutoff, mal_link
from test_support import create_test_catalog, test_challenge


class PageParser(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.tags = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class PublicSiteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "site.db"
        self.records = create_test_catalog(self.path)
        self.challenge = test_challenge(self.records)
        self.challenge_id = database.record_challenge(self.challenge, "2026-09-20", self.path)
        for index, (day, state) in enumerate((
            ("2026-09-21", "approved"), ("2026-09-22", "approved"),
            ("2026-09-19", "draft"),
        ), start=1):
            database.record_challenge(test_challenge(self.records, index * 24), day,
                                      self.path, publication_state=state)
        self.previous_path = web.app.state.database_path
        self.previous_limiter = web.app.state.feedback_limiter
        web.app.state.database_path = self.path
        web.app.state.feedback_limiter = FeedbackRateLimiter()
        self.client = TestClient(web.app)
        self.cutoff = patch("public_pages.historical_cutoff", return_value=date(2026, 9, 21))
        self.sitemap_cutoff = patch("app.historical_cutoff", return_value=date(2026, 9, 21))
        self.cutoff.start()
        self.sitemap_cutoff.start()

    def tearDown(self):
        self.client.close()
        self.cutoff.stop()
        self.sitemap_cutoff.stop()
        web.app.state.database_path = self.previous_path
        web.app.state.feedback_limiter = self.previous_limiter
        self.temp.cleanup()

    def rows(self, table):
        with closing(sqlite3.connect(self.path)) as connection:
            return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def report(self, **kwargs):
        return {"id": str(uuid4()), "kind": "bug", "message": "The test comparison has a display issue.", **kwargs}

    def submit(self, report, **kwargs):
        return self.client.post("/feedback/submit", json=report,
                                headers={"Origin": "http://testserver"}, **kwargs)

    def test_all_pages_are_server_html_with_metadata_and_working_links(self):
        titles = set()
        for path in STATIC_PAGES:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertTrue(response.headers["content-type"].startswith("text/html"))
            self.assertNotIn("Advertisement placeholder", response.text)
            self.assertNotIn("ADVERTISEMENT", response.text)
            self.assertNotIn("ad-slot", response.text)
            parser = PageParser(response.text)
            self.assertGreaterEqual(sum(tag == "h1" for tag, _ in parser.tags), 1, path)
            if path != "/":
                self.assertEqual(sum(tag == "h1" for tag, _ in parser.tags), 1, path)
            self.assertTrue(any(tag == "meta" and a.get("name") == "description"
                                and a.get("content") for tag, a in parser.tags))
            self.assertTrue(any(tag == "link" and a.get("rel") == "canonical"
                                and a["href"] == f"https://animoredle.com{path}"
                                for tag, a in parser.tags))
            self.assertTrue(any(tag == "meta" and a.get("name") == "google-adsense-account"
                                and a.get("content") == "ca-pub-7045982233365027"
                                for tag, a in parser.tags))
            title = response.text.split("<title>")[1].split("</title>")[0]
            self.assertNotIn(title, titles)
            titles.add(title)
            for tag, attributes in parser.tags:
                href = attributes.get("href", "")
                if tag == "a" and href.startswith("/"):
                    self.assertEqual(self.client.get(href).status_code, 200, href)
        homepage = self.client.get("/").text
        self.assertIn('class="site-introduction"', homepage)
        self.assertIn("20 comparisons", homepage)
        self.assertEqual(self.client.get("/missing-page").status_code, 404)
        self.assertEqual(self.client.get("/ads.txt").text.strip(),
                         "google.com, pub-7045982233365027, DIRECT, f08c47fec0942fa0")

    def test_recaps_are_bounded_read_only_and_exclude_active_future_drafts(self):
        tables = ("challenge_runs", "challenge_anime", "matchup_history", "players", "player_answers", "player_results")
        before = {table: self.rows(table) for table in tables}
        index = self.client.get("/past-challenges")
        self.assertIn("/past-challenges/2026-09-20", index.text)
        for day in ("2026-09-19", "2026-09-21", "2026-09-22", "2026-09-18", "2026-09-30"):
            self.assertNotIn(f"/past-challenges/{day}", index.text)
            self.assertEqual(self.client.get(f"/past-challenges/{day}").status_code, 404)
        recap = self.client.get("/past-challenges/2026-09-20")
        self.assertEqual(recap.status_code, 200)
        self.assertEqual(recap.text.count("Correct choice:"), 20)
        self.assertEqual(recap.text.count('<h3>Comparison '), 20)
        self.assertIn("series episodes", recap.text)
        self.assertIn("Popularity rank #", recap.text)
        self.assertIn("Release date: 2000-01-", recap.text)
        self.assertIn("Score: 6", recap.text)
        self.assertIn("Not enough official answers", recap.text)
        self.assertEqual(before, {table: self.rows(table) for table in tables})
        queries = []
        actual_connect = database._connect_database
        def traced(path):
            connection = actual_connect(path)
            connection.set_trace_callback(queries.append)
            return connection
        with patch.object(database, "_connect_database", side_effect=traced):
            database.load_public_archive_records(date(2026, 9, 21), date(2026, 9, 19), self.path)
        self.assertEqual(sum(q.lstrip().upper().startswith("SELECT") for q in queries), 2)
        with self.assertRaises(ValueError):
            database.load_public_archive_records(date.today(), date(2026, 9, 19), self.path, limit=10000)

    def test_empty_legacy_invalid_and_unresolved_recaps_fail_safely(self):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("UPDATE anime SET title = '<script>alert(1)</script>' WHERE mal_id = 200000")
            connection.commit()
        recap = self.client.get("/past-challenges/2026-09-20")
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", recap.text)
        self.assertNotIn("<script>alert(1)</script>", recap.text)
        # An incomplete component cannot yield a confident series representative.
        database.store_anime_relations(200012, [{"mal_id": 299999, "relation_type": "sequel"}], self.path)
        self.assertEqual(self.client.get("/past-challenges/2026-09-20").status_code, 404)
        self.assertNotIn("/past-challenges/2026-09-20", self.client.get("/past-challenges").text)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("DELETE FROM challenge_anime WHERE challenge_id = ? AND position = 6", (self.challenge_id,))
            connection.commit()
        self.assertEqual(self.client.get("/past-challenges/2026-09-20").status_code, 404)
        self.assertIn("No published recaps", self.client.get("/past-challenges").text)

    def test_recaps_use_root_links_and_anonymous_aggregates(self):
        root_id, sequel_id = 200012, 200013
        database.store_anime_relations(root_id, [{"mal_id": sequel_id, "relation_type": "sequel"}], self.path)
        database.store_anime_relations(sequel_id, [{"mal_id": root_id, "relation_type": "prequel"}], self.path)
        for index in range(3):
            player = f"private-player-{index}"
            database.ensure_player(player, self.path)
            database.record_player_answer(player, self.challenge_id, "Higher Score", 1,
                                          200001, 200001, index < 2, self.path)
        html = self.client.get("/past-challenges/2026-09-20").text
        episodes = html.split('<h2>More Episodes</h2>')[1].split('<h2>More Recent</h2>')[0]
        self.assertNotIn(f"https://myanimelist.net/anime/{sequel_id}", episodes)
        self.assertIn(f"https://myanimelist.net/anime/{root_id}", episodes)
        self.assertIn("14 series episodes", episodes)
        self.assertIn("66.7% recorded correct in original official answers", html)
        self.assertIn("not re-scored after catalog updates", html)
        self.assertNotIn("private-player", html)
        for invalid in (-1, 0, True, "123", None):
            self.assertNotIn("href=", mal_link(invalid, "Name"))
        self.assertIn('rel="noopener noreferrer"', mal_link(123, "Name"))

    def test_sitemap_robots_and_noindex_do_not_leak_player_or_active_data(self):
        response = self.client.get("/sitemap.xml")
        root = ElementTree.fromstring(response.text)
        urls = [element.text for element in root.iter() if element.tag.endswith("}loc")]
        self.assertIn("https://animoredle.com/past-challenges/2026-09-20", urls)
        self.assertNotIn("https://animoredle.com/past-challenges/2026-09-21", urls)
        self.assertEqual(len(urls), len(set(urls)))
        for url in urls:
            self.assertEqual(self.client.get(url.replace("https://animoredle.com", "")).status_code, 200)
        self.assertNotIn("lastmod", response.text)
        robots = self.client.get("/robots.txt").text
        self.assertIn("Allow: /", robots)
        self.assertNotIn("Disallow: /static", robots)
        self.assertIn("https://animoredle.com/sitemap.xml", robots)
        for path in ("/challenge/2026-09-20", "/archive", "/player/history", "/static/about.html", "/?date=2026-09-20"):
            self.assertEqual(self.client.get(path).headers.get("x-robots-tag"), "noindex", path)

    def test_official_local_date_progress_survives_content_changes(self):
        response = self.client.get("/challenge/today?local_date=2026-09-20")
        payload = response.json()
        self.assertEqual(payload["challenge_date"], "2026-09-20")
        self.assertEqual(len(payload["categories"]), 4)
        for category in payload["categories"]:
            for anime in category["anime"]:
                self.assertTrue(set(anime).isdisjoint({"score", "members", "series_episodes", "release_date"}))
        for category in payload["categories"][:2]:
            for position in range(1, 6 if category["name"] == "Higher Score" else 2):
                self.assertEqual(self.client.post("/challenge/2026-09-20/answer", json={
                    "category": category["name"], "comparison_position": position,
                    "selected_mal_id": category["anime"][position - 1]["mal_id"],
                }).status_code, 200)
        progress = self.client.get("/challenge/today?local_date=2026-09-20").json()["progress"]
        self.assertEqual(len(progress["answers"]), 6)
        self.assertEqual(progress["next"], {"category": "More Popular", "comparison_position": 2})
        self.assertEqual(self.client.get("/challenge/today?local_date=2026-09-23").status_code, 404)

    def test_feedback_persists_both_kinds_idempotently_and_privately(self):
        report = self.report(email="visitor@example.org", expected_behavior="A readable title")
        self.assertEqual(self.submit(report).status_code, 200)
        self.assertEqual(self.submit(report).status_code, 200)
        self.assertEqual(self.rows("feedback_submissions"), 1)
        self.assertEqual(self.submit({**report, "message": "A different report using the same ID."}).status_code, 409)
        self.assertEqual(self.submit(self.report(kind="feedback", category="Suggestion")).status_code, 200)
        database.initialize_database(self.path)
        database.initialize_database(self.path)
        reports = database.load_feedback_submissions(self.path)
        self.assertEqual(len(reports), 2)
        self.assertEqual(reports[1]["email"], "visitor@example.org")
        self.assertEqual(self.client.get("/feedback/submissions").status_code, 404)
        self.assertEqual(self.client.get("/feedback/submit").status_code, 405)
        self.assertEqual(self.rows("players"), 0)

    def test_feedback_validation_origin_size_and_honeypot(self):
        for update in ({"message": "short"}, {"message": "a" * 5001}, {"email": "invalid"},
                       {"challenge_date": "2026-02-30"}, {"extra": "bad"}, {"id": "bad"},
                       {"category": "unrecognized"}, {"message": "Control character\x00 in message"}):
            self.assertEqual(self.submit(self.report(**update)).status_code, 422, update.keys())
        self.assertEqual(self.client.post("/feedback/submit", json=self.report(), headers={"Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.client.post("/feedback/submit", content="x", headers={"Origin": "http://testserver"}).status_code, 415)
        self.assertEqual(self.client.post("/feedback/submit", content="x" * 25000,
                                         headers={"Origin": "http://testserver", "Content-Type": "application/json"}).status_code, 413)
        self.assertEqual(self.client.post("/feedback/submit", content=iter([b"x" * 25000]),
                                         headers={"Origin": "http://testserver", "Content-Type": "application/json"}).status_code, 413)
        self.assertEqual(self.submit(self.report(website="spam.example")).status_code, 200)
        self.assertEqual(self.rows("feedback_submissions"), 0)

    def test_rate_limit_uses_connection_peer_not_forwarded_header(self):
        for index in range(MAX_PER_PEER):
            response = self.client.post("/feedback/submit", json=self.report(website="bot"),
                headers={"Origin": "http://testserver", "X-Forwarded-For": f"192.0.2.{index}"})
            self.assertEqual(response.status_code, 200)
        self.assertEqual(self.submit(self.report()).status_code, 429)
        limiter = FeedbackRateLimiter()
        self.assertTrue(limiter.allow("peer", now=0))
        self.assertTrue(limiter.allow("peer", now=601))
        self.assertTrue(all(isinstance(key, bytes) for key in limiter._peers))

    def test_archive_database_failures_are_503_and_logs_are_sanitized(self):
        secret = "postgresql://private-user:secret-password@private-host/db player-id-private"
        for helper in ("load_challenge_record", "load_series_representatives", "load_comparison_answer_stats"):
            with self.subTest(helper=helper), patch(f"public_pages.{helper}", side_effect=sqlite3.OperationalError(secret)):
                with self.assertLogs("app", level="ERROR") as logs:
                    response = self.client.get("/past-challenges/2026-09-20")
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.headers["x-robots-tag"], "noindex")
                self.assertEqual(response.headers["retry-after"], "60")
                self.assertIn("2026-09-20", logs.output[0])
                self.assertIn("OperationalError", logs.output[0])
                self.assertIn("frames=", logs.output[0])
                for value in (secret, "secret-password", "private-host", "player-id-private"):
                    self.assertNotIn(value, response.text)
                    self.assertNotIn(value, " ".join(logs.output))
        with patch("app.archive_detail", side_effect=RuntimeError(secret)), self.assertLogs("app", level="ERROR"):
            self.assertEqual(self.client.get("/past-challenges/2026-09-20").status_code, 503)
        class DatabaseUnavailable(Exception):
            sqlstate = "08006"
        with patch("app.archive_detail", side_effect=DatabaseUnavailable(secret)):
            with self.assertLogs("app", level="ERROR") as logs:
                self.assertEqual(self.client.get("/past-challenges/2026-09-20").status_code, 503)
            self.assertIn("sqlstate=08006", logs.output[0])
            self.assertNotIn("secret-password", logs.output[0])
        for day in ("not-a-date", "2026-02-30", "2026-09-19", "2026-09-18", "2026-09-21"):
            self.assertEqual(self.client.get(f"/past-challenges/{day}").status_code, 404)
        with patch("public_pages.load_challenge_record", return_value=None):
            self.assertEqual(self.client.get("/past-challenges/2026-09-20").status_code, 404)

    def test_recaps_keep_recorded_statistics_when_current_metrics_change(self):
        for index in range(3):
            player = f"historical-{index}"
            database.ensure_player(player, self.path)
            database.record_player_answer(player, self.challenge_id, "Higher Score", 1,
                                          200001, 200001, index < 2, self.path)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("UPDATE anime SET score = 9.99 WHERE mal_id = 200000")
            connection.commit()
        html = self.client.get("/past-challenges/2026-09-20").text
        self.assertIn("Score: 9.99", html)
        self.assertIn("Correct choice: Isolated catalog entry 0", html)
        self.assertIn("66.7% recorded correct in original official answers", html)
        self.assertIn("not a frozen snapshot", html)
        self.assertEqual(self.rows("player_answers"), 3)

    def test_trusted_ingress_limits_clients_independently_and_ignores_spoofed_prefix(self):
        web.app.state.feedback_limiter = FeedbackRateLimiter("10.42.0.1/32")
        with TestClient(web.app, client=("10.42.0.1", 12345)) as proxied:
            for index in range(MAX_PER_PEER):
                response = proxied.post("/feedback/submit", json=self.report(website="bot"),
                    headers={"Origin": "http://testserver", "X-Forwarded-For": f"198.51.100.{index}, 192.0.2.1"})
                self.assertEqual(response.status_code, 200)
            blocked = proxied.post("/feedback/submit", json=self.report(website="bot"),
                headers={"Origin": "http://testserver", "X-Forwarded-For": "198.51.100.99, 192.0.2.1"})
            self.assertEqual(blocked.status_code, 429)
            other = proxied.post("/feedback/submit", json=self.report(website="bot"),
                headers={"Origin": "http://testserver", "X-Forwarded-For": "192.0.2.2"})
            self.assertEqual(other.status_code, 200)
        self.assertEqual(self.rows("feedback_submissions"), 0)

    def test_proxy_trust_is_opt_in_bounded_and_fail_closed(self):
        def request(headers, peer="10.42.0.1"):
            return Request({"type": "http", "client": (peer, 1234), "headers": headers})
        trusted = FeedbackRateLimiter("10.42.0.1/32")
        untrusted = FeedbackRateLimiter("")
        header = [(b"x-forwarded-for", b"192.0.2.1")]
        self.assertEqual(untrusted.request_peer(request(header)), "10.42.0.1")
        self.assertEqual(trusted.request_peer(request(header, "10.42.0.2")), "10.42.0.2")
        self.assertEqual(trusted.request_peer(request(header)), "192.0.2.1")
        for headers in ([], header * 2, [(b"x-forwarded-for", b"192.0.2.1, invalid")],
                        [(b"x-forwarded-for", b"x" * 1025)], [(b"x-forwarded-for", b"fe80::1%spoof")]):
            self.assertEqual(trusted.request_peer(request(headers)), "10.42.0.1")
        for invalid in ("*", "0.0.0.0/0", "::/0", "not-a-network"):
            with self.assertRaises(ValueError):
                FeedbackRateLimiter(invalid)
        limiter = FeedbackRateLimiter("")
        for index in range(100):
            self.assertTrue(limiter.allow(f"peer-{index}", now=1))
        self.assertFalse(limiter.allow("another-peer", now=1))

    def test_feedback_failure_is_honest_and_cli_retention_is_private(self):
        with patch("feedback.save_feedback_submission", side_effect=RuntimeError("secret")):
            response = self.submit(self.report())
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("secret", response.text)
        report = self.report(email="private@example.org")
        self.submit(report)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("UPDATE feedback_submissions SET created_at = '2000-01-01T00:00:00+00:00'")
            connection.commit()
        def command(*args):
            output = io.StringIO()
            with redirect_stdout(output):
                feedback_cli(["--database-path", str(self.path), *args])
            return output.getvalue()
        listing = command("list")
        self.assertNotIn("private@example.org", listing)
        self.assertIn("private@example.org", command("show", report["id"]))
        self.assertIn("Would delete 1", command("prune"))
        self.assertEqual(self.rows("feedback_submissions"), 1)
        self.assertIn("Deleted 1", command("prune", "--apply"))
        self.assertEqual(self.rows("feedback_submissions"), 0)
        self.assertEqual(self.rows("challenge_runs"), 4)

    def test_additive_feedback_migration_preserves_game_and_player_results(self):
        database.ensure_player("migration-test-player", self.path)
        database.record_player_result("migration-test-player", self.challenge_id, 14, self.path)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("DROP TABLE feedback_submissions")
            connection.commit()
        database.initialize_database(self.path)
        database.initialize_database(self.path)
        self.assertEqual(self.rows("feedback_submissions"), 0)
        self.assertEqual(self.rows("challenge_runs"), 4)
        self.assertEqual(database.load_player_results("migration-test-player", self.path)[0]["score"], 14)

    def test_official_standings_and_immutable_answers_regression(self):
        for index, score in enumerate((18, 18, 14)):
            player = f"standing-{index}"
            database.ensure_player(player, self.path)
            database.record_player_result(player, self.challenge_id, score, self.path)
        database.ensure_player("incomplete", self.path)
        for player, score, expected in (("standing-0", 18, 33), ("standing-1", 18, 33), ("standing-2", 14, 100)):
            result = database.load_player_result_standing(player, self.challenge_id, score, self.path)
            self.assertEqual(result["completed_players"], 3)
            self.assertEqual(result["top_percent"], expected)
        result = database.record_player_result("standing-2", self.challenge_id, 20, self.path)
        self.assertEqual(result["score"], 14)
        payload = {"category": "Higher Score", "comparison_position": 1, "selected_mal_id": 200001}
        self.assertEqual(self.client.post("/challenge/2026-09-20/answer", json=payload).status_code, 200)
        repeated = self.client.post("/challenge/2026-09-20/answer", json={**payload, "selected_mal_id": 200000})
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(repeated.json()["selected_mal_id"], 200001)
        self.assertEqual(self.rows("player_answers"), 1)

    def test_legacy_history_and_results_are_preserved_but_not_recapped(self):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.executemany(
                "INSERT INTO challenge_anime (challenge_id, category, position, mal_id) VALUES (?, ?, ?, ?)",
                [(self.challenge_id, "Longer Runtime", position, 200167 + position)
                 for position in range(1, 7)],
            )
            connection.executemany(
                "INSERT INTO matchup_history (challenge_id, category, anime_a_id, anime_b_id, challenge_date) VALUES (?, ?, ?, ?, ?)",
                [(self.challenge_id, "Longer Runtime", 200167 + position, 200168 + position, "2026-09-20")
                 for position in range(1, 6)],
            )
            connection.commit()
        database.ensure_player("legacy-player", self.path)
        database.record_player_result("legacy-player", self.challenge_id, 12, self.path)
        self.assertEqual(self.client.get("/challenge/2026-09-20").status_code, 409)
        self.assertEqual(self.client.get("/past-challenges/2026-09-20").status_code, 404)
        self.assertNotIn("/past-challenges/2026-09-20", self.client.get("/past-challenges").text)
        result = database.load_player_results("legacy-player", self.path)[0]
        self.assertEqual((result["score"], result["total_questions"]), (12, 25))
        self.assertEqual(self.rows("challenge_anime"), 102)

    def test_worldwide_cutoff_and_postgres_feedback_schema_translation(self):
        self.assertEqual(historical_cutoff(datetime(2026, 9, 22, 3, tzinfo=timezone.utc)), date(2026, 9, 21))
        from unittest.mock import Mock
        raw = Mock()
        with patch("psycopg.connect", return_value=raw):
            adapter = database._PostgresConnection("postgresql://test-only.invalid/test")
        adapter.executescript(database.CREATE_FEEDBACK_TABLE)
        sql = " ".join(call.args[0] for call in raw.execute.call_args_list)
        self.assertIn("CREATE TABLE IF NOT EXISTS feedback_submissions", sql)
        self.assertIn("CREATE INDEX IF NOT EXISTS", sql)
        adapter.execute("SELECT * FROM feedback_submissions WHERE id = ?", ("report",))
        self.assertIn("%s", raw.execute.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
