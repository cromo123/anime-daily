"""Copy the existing SQLite catalog and history into an empty PostgreSQL database."""

import os
import sqlite3
from pathlib import Path

from database import DATABASE_PATH, _connect_database, initialize_database


TABLES_IN_ORDER = (
    "anime",
    "anime_relations",
    "catalog_ingestion_failures",
    "ingestion_state",
    "challenge_runs",
    "challenge_anime",
    "matchup_history",
    "players",
    "player_results",
    "player_answers",
)

TABLE_COLUMNS = {
    "anime": (
        "mal_id", "title", "image_url", "score", "popularity_rank", "members",
        "entry_episodes", "series_episodes", "release_date", "type",
        "runtime_minutes", "relations_fetched",
    ),
    "anime_relations": ("source_mal_id", "target_mal_id", "relation_type"),
    "catalog_ingestion_failures": ("mal_id", "title", "stage", "reason", "updated_at"),
    "ingestion_state": ("key", "value"),
    "challenge_runs": ("id", "challenge_date", "created_at"),
    "challenge_anime": ("challenge_id", "category", "position", "mal_id"),
    "matchup_history": (
        "challenge_id", "category", "anime_a_id", "anime_b_id", "challenge_date",
    ),
    "players": ("id", "created_at"),
    "player_results": ("player_id", "challenge_id", "score", "completed_at"),
    "player_answers": (
        "player_id", "challenge_id", "category", "comparison_position",
        "selected_mal_id", "correct_mal_id", "correct", "answered_at",
    ),
}


def _open_sqlite_read_only(path):
    resolved = Path(path).resolve()
    if not resolved.exists():
        raise RuntimeError(f"SQLite source not found: {resolved}")
    return sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True)


def _destination_is_empty(connection):
    counts = {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in TABLES_IN_ORDER
    }
    populated = {table: count for table, count in counts.items() if count}
    if populated:
        raise RuntimeError(
            "Refusing migration: PostgreSQL already contains application rows: "
            + ", ".join(f"{table}={count}" for table, count in populated.items())
        )


def _copy_table(source, destination, table):
    columns = TABLE_COLUMNS[table]
    quoted_columns = ", ".join(columns)
    placeholders = ", ".join("?" for _ in columns)
    rows = source.execute(
        f"SELECT {quoted_columns} FROM {table}"
    ).fetchall()
    if rows:
        destination.executemany(
            f"INSERT INTO {table} ({quoted_columns}) VALUES ({placeholders})",
            rows,
        )
    return len(rows)


def _reset_challenge_sequence(destination, challenge_count):
    if challenge_count:
        maximum = destination.execute(
            "SELECT MAX(id) FROM challenge_runs"
        ).fetchone()[0]
        destination.execute(
            "SELECT setval(pg_get_serial_sequence('challenge_runs', 'id'), ?, true)",
            (maximum,),
        )
        return maximum

    destination.execute(
        "SELECT setval(pg_get_serial_sequence('challenge_runs', 'id'), 1, false)"
    )
    return 0


def _verify_migration(source, destination, expected_counts):
    actual_counts = {
        table: destination.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in TABLES_IN_ORDER
    }
    if actual_counts != expected_counts:
        raise RuntimeError(
            f"Row-count verification failed: expected {expected_counts}, "
            f"got {actual_counts}"
        )

    checks = [
        ("anime", "mal_id", "SELECT mal_id FROM anime ORDER BY mal_id LIMIT 1"),
        (
            "challenge_runs",
            "id",
            "SELECT id FROM challenge_runs ORDER BY id LIMIT 1",
        ),
        ("players", "id", "SELECT id FROM players ORDER BY id LIMIT 1"),
    ]
    for table, key, query in checks:
        source_value = source.execute(
            f"SELECT {key} FROM {table} ORDER BY {key} LIMIT 1"
        ).fetchone()
        destination_value = destination.execute(query).fetchone()
        if (source_value[0] if source_value else None) != (
            destination_value[0] if destination_value else None
        ):
            raise RuntimeError(f"Representative {table} record verification failed.")

    orphan_checks = (
        "SELECT COUNT(*) FROM anime_relations r LEFT JOIN anime a ON a.mal_id = r.source_mal_id WHERE a.mal_id IS NULL",
        "SELECT COUNT(*) FROM challenge_anime ca LEFT JOIN challenge_runs c ON c.id = ca.challenge_id WHERE c.id IS NULL",
        "SELECT COUNT(*) FROM player_answers pa LEFT JOIN players p ON p.id = pa.player_id WHERE p.id IS NULL",
    )
    if any(destination.execute(query).fetchone()[0] for query in orphan_checks):
        raise RuntimeError("Foreign-key verification found orphaned rows.")


def migrate(source_path=DATABASE_PATH):
    if not os.getenv("DATABASE_URL"):
        raise RuntimeError("DATABASE_URL is required for SQLite-to-PostgreSQL migration.")

    initialize_database()
    source = _open_sqlite_read_only(source_path)
    destination = _connect_database(DATABASE_PATH)

    try:
        _destination_is_empty(destination)
        counts = {}
        for table in TABLES_IN_ORDER:
            counts[table] = _copy_table(source, destination, table)

        sequence_max = _reset_challenge_sequence(
            destination, counts["challenge_runs"]
        )
        destination.commit()
        _verify_migration(source, destination, counts)
    except Exception:
        destination.rollback()
        raise
    finally:
        source.close()
        destination.close()

    return counts, sequence_max


def main():
    counts, sequence_max = migrate()
    print("SQLite → PostgreSQL migration complete")
    print("Table                       SQLite rows  PostgreSQL rows")
    for table, count in counts.items():
        print(f"{table:28} {count:11}  {count:15}")
    print(f"challenge_runs sequence advanced through ID {sequence_max}")


if __name__ == "__main__":
    main()
