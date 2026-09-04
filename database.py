import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


DATABASE_PATH = Path(__file__).parent / "data" / "anime_daily.db"

CREATE_ANIME_TABLE = """
CREATE TABLE IF NOT EXISTS anime (
    mal_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    score REAL,
    popularity_rank INTEGER,
    members INTEGER,
    entry_episodes INTEGER,
    series_episodes INTEGER,
    release_date TEXT,
    type TEXT,
    runtime_minutes INTEGER
)
"""

CREATE_HISTORY_TABLES = """
CREATE TABLE IF NOT EXISTS challenge_runs (
    id INTEGER PRIMARY KEY,
    challenge_date TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS challenge_anime (
    challenge_id INTEGER NOT NULL,
    category TEXT NOT NULL,
    position INTEGER NOT NULL CHECK (position BETWEEN 1 AND 6),
    mal_id INTEGER NOT NULL,
    PRIMARY KEY (challenge_id, category, position),
    UNIQUE (challenge_id, category, mal_id),
    UNIQUE (challenge_id, mal_id),
    FOREIGN KEY (challenge_id) REFERENCES challenge_runs(id) ON DELETE CASCADE,
    FOREIGN KEY (mal_id) REFERENCES anime(mal_id)
);

CREATE TABLE IF NOT EXISTS matchup_history (
    challenge_id INTEGER NOT NULL,
    category TEXT NOT NULL,
    anime_a_id INTEGER NOT NULL,
    anime_b_id INTEGER NOT NULL,
    challenge_date TEXT NOT NULL,
    PRIMARY KEY (challenge_id, category, anime_a_id, anime_b_id),
    UNIQUE (challenge_id, anime_a_id, anime_b_id),
    CHECK (anime_a_id < anime_b_id),
    FOREIGN KEY (challenge_id) REFERENCES challenge_runs(id) ON DELETE CASCADE,
    FOREIGN KEY (anime_a_id) REFERENCES anime(mal_id),
    FOREIGN KEY (anime_b_id) REFERENCES anime(mal_id)
);

CREATE INDEX IF NOT EXISTS challenge_runs_date_index
ON challenge_runs(challenge_date);

CREATE INDEX IF NOT EXISTS matchup_history_date_index
ON matchup_history(challenge_date);
"""

UPSERT_ANIME = """
INSERT INTO anime (
    mal_id,
    title,
    score,
    popularity_rank,
    members,
    entry_episodes,
    series_episodes,
    release_date,
    type,
    runtime_minutes
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(mal_id) DO UPDATE SET
    title = excluded.title,
    score = excluded.score,
    popularity_rank = excluded.popularity_rank,
    members = excluded.members,
    entry_episodes = excluded.entry_episodes,
    series_episodes = excluded.series_episodes,
    release_date = excluded.release_date,
    type = excluded.type,
    runtime_minutes = excluded.runtime_minutes
"""


def _connect_database(database_path):
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _parse_challenge_date(challenge_date):
    if isinstance(challenge_date, date):
        return challenge_date

    try:
        return date.fromisoformat(challenge_date)
    except (TypeError, ValueError) as error:
        raise ValueError("challenge_date must use YYYY-MM-DD format.") from error


def normalize_matchup_pair(anime_a_id, anime_b_id):
    if anime_a_id == anime_b_id:
        raise ValueError("A matchup must contain two different MAL IDs.")

    return min(anime_a_id, anime_b_id), max(anime_a_id, anime_b_id)


def initialize_database(database_path=DATABASE_PATH):
    database_path = Path(database_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = _connect_database(database_path)

    try:
        connection.execute(CREATE_ANIME_TABLE)
        connection.executescript(CREATE_HISTORY_TABLES)
        connection.commit()
    finally:
        connection.close()


def upsert_anime_records(anime_records, database_path=DATABASE_PATH):
    anime_records = list(anime_records)
    initialize_database(database_path)

    rows = [
        (
            anime.get("mal_id"),
            anime.get("title"),
            anime.get("score"),
            anime.get("popularity_rank"),
            anime.get("members"),
            anime.get("entry_episodes"),
            anime.get("series_episodes"),
            anime.get("release_date"),
            anime.get("type"),
            anime.get("runtime_minutes"),
        )
        for anime in anime_records
    ]

    connection = _connect_database(database_path)

    try:
        connection.executemany(UPSERT_ANIME, rows)
        connection.commit()
    finally:
        connection.close()

    return len(rows)


def load_anime_records(database_path=DATABASE_PATH):
    database_path = Path(database_path)

    if not database_path.exists():
        raise RuntimeError(
            f"Anime database not found at {database_path}. Run import_catalog.py first."
        )

    connection = _connect_database(database_path)
    connection.row_factory = sqlite3.Row

    try:
        rows = connection.execute(
            """
            SELECT
                mal_id,
                title,
                score,
                popularity_rank,
                members,
                entry_episodes,
                series_episodes,
                release_date,
                type,
                runtime_minutes
            FROM anime
            ORDER BY mal_id
            """
        ).fetchall()
    except sqlite3.OperationalError as error:
        raise RuntimeError(
            f"Anime database at {database_path} is not initialized. "
            "Run import_catalog.py first."
        ) from error
    finally:
        connection.close()

    return [dict(row) for row in rows]


def load_recent_anime_ids(challenge_date, recent_days, database_path=DATABASE_PATH):
    challenge_date = _parse_challenge_date(challenge_date)
    earliest_date = (challenge_date - timedelta(days=recent_days)).isoformat()
    latest_date = challenge_date.isoformat()
    initialize_database(database_path)
    connection = _connect_database(database_path)

    try:
        rows = connection.execute(
            """
            SELECT DISTINCT challenge_anime.mal_id
            FROM challenge_anime
            JOIN challenge_runs
                ON challenge_runs.id = challenge_anime.challenge_id
            WHERE challenge_runs.challenge_date BETWEEN ? AND ?
            """,
            (earliest_date, latest_date),
        ).fetchall()
    finally:
        connection.close()

    return {row[0] for row in rows}


def load_recent_matchup_pairs(
    challenge_date,
    recent_days,
    database_path=DATABASE_PATH,
):
    challenge_date = _parse_challenge_date(challenge_date)
    earliest_date = (challenge_date - timedelta(days=recent_days)).isoformat()
    latest_date = challenge_date.isoformat()
    initialize_database(database_path)
    connection = _connect_database(database_path)

    try:
        rows = connection.execute(
            """
            SELECT DISTINCT anime_a_id, anime_b_id
            FROM matchup_history
            WHERE challenge_date BETWEEN ? AND ?
            """,
            (earliest_date, latest_date),
        ).fetchall()
    finally:
        connection.close()

    return {normalize_matchup_pair(row[0], row[1]) for row in rows}


def record_challenge(
    challenge,
    challenge_date,
    database_path=DATABASE_PATH,
    created_at=None,
):
    if len(challenge) != 5 or any(
        len(category.get("anime", [])) != 6 for category in challenge
    ):
        raise ValueError(
            "A complete challenge must contain five categories with six anime each."
        )

    challenge_date = _parse_challenge_date(challenge_date).isoformat()

    if created_at is None:
        created_at = datetime.now(timezone.utc).isoformat()

    initialize_database(database_path)
    connection = _connect_database(database_path)

    try:
        cursor = connection.execute(
            """
            INSERT INTO challenge_runs (challenge_date, created_at)
            VALUES (?, ?)
            """,
            (challenge_date, created_at),
        )
        challenge_id = cursor.lastrowid

        for category in challenge:
            category_name = category["name"]
            category_anime = category["anime"]

            for position, anime in enumerate(category_anime, start=1):
                connection.execute(
                    """
                    INSERT INTO challenge_anime (
                        challenge_id,
                        category,
                        position,
                        mal_id
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (challenge_id, category_name, position, anime["mal_id"]),
                )

            for index in range(len(category_anime) - 1):
                anime_a_id, anime_b_id = normalize_matchup_pair(
                    category_anime[index]["mal_id"],
                    category_anime[index + 1]["mal_id"],
                )
                connection.execute(
                    """
                    INSERT INTO matchup_history (
                        challenge_id,
                        category,
                        anime_a_id,
                        anime_b_id,
                        challenge_date
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        challenge_id,
                        category_name,
                        anime_a_id,
                        anime_b_id,
                        challenge_date,
                    ),
                )

        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return challenge_id
