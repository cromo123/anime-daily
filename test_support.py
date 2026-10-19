"""Small catalog fixtures confined to explicit, temporary SQLite databases."""

from datetime import date, timedelta
from pathlib import Path
import sqlite3
from contextlib import closing

from challenge import CATEGORY_RULES
from database import DATABASE_PATH, initialize_database, upsert_anime_records


def create_test_catalog(database_path, count=192):
    if Path(database_path).resolve() == DATABASE_PATH.resolve():
        raise ValueError("Test fixtures must never use the application database.")
    initialize_database(database_path)
    records = [
        {
            "mal_id": 200000 + index,
            "title": f"Isolated catalog entry {index}",
            "image_url": None,
            "type": "tv",
            "score": 6 + index / 100,
            "popularity_rank": index + 1,
            "members": 10000 + index * 2000,
            "entry_episodes": index + 1,
            "series_episodes": index + 1,
            "release_date": (date(2000, 1, 1) + timedelta(days=index)).isoformat(),
            "runtime_minutes": 24,
        }
        for index in range(count)
    ]
    upsert_anime_records(records, database_path)
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("UPDATE anime SET relations_fetched = 1")
        connection.commit()
    return records


def test_challenge(records, offset=0):
    return [
        {**rule, "anime": records[offset + index * 6:offset + index * 6 + 6]}
        for index, rule in enumerate(CATEGORY_RULES)
    ]
