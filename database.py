import sqlite3
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


def initialize_database(database_path=DATABASE_PATH):
    database_path = Path(database_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)

    try:
        connection.execute(CREATE_ANIME_TABLE)
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

    connection = sqlite3.connect(database_path)

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

    connection = sqlite3.connect(database_path)
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
