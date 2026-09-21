"""Copy the existing SQLite catalog and history into an empty PostgreSQL database."""

import os
import sqlite3
from pathlib import Path

from database import DATABASE_PATH, _connect_database, initialize_database
from fixture_safety import (
    KNOWN_SYNTHETIC_MAL_IDS,
    has_unexpected_synthetic_fixture_title,
    is_known_synthetic_fixture,
)


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
    "challenge_runs": (
        "id", "challenge_date", "created_at", "publication_state"
    ),
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


def _row_values(table, row):
    return dict(zip(TABLE_COLUMNS[table], row))


def _row_is_safe(table, row, contaminated_challenge_ids):
    values = _row_values(table, row)

    if table == "anime":
        return not is_known_synthetic_fixture(values["mal_id"], values["title"])
    if table == "anime_relations":
        return (
            values["source_mal_id"] not in KNOWN_SYNTHETIC_MAL_IDS
            and values["target_mal_id"] not in KNOWN_SYNTHETIC_MAL_IDS
        )
    if table == "catalog_ingestion_failures":
        return values["mal_id"] not in KNOWN_SYNTHETIC_MAL_IDS
    if table in {"challenge_runs", "challenge_anime", "matchup_history"}:
        challenge_key = "id" if table == "challenge_runs" else "challenge_id"
        if values[challenge_key] in contaminated_challenge_ids:
            return False
    if table == "challenge_anime":
        return values["mal_id"] not in KNOWN_SYNTHETIC_MAL_IDS
    if table == "matchup_history":
        return (
            values["anime_a_id"] not in KNOWN_SYNTHETIC_MAL_IDS
            and values["anime_b_id"] not in KNOWN_SYNTHETIC_MAL_IDS
        )
    if table in {"player_results", "player_answers"}:
        if values["challenge_id"] in contaminated_challenge_ids:
            return False
    if table == "player_answers":
        return (
            values["selected_mal_id"] not in KNOWN_SYNTHETIC_MAL_IDS
            and values["correct_mal_id"] not in KNOWN_SYNTHETIC_MAL_IDS
        )
    return True


def _scan_source(source):
    anime_rows = source.execute("SELECT mal_id, title FROM anime").fetchall()
    failure_rows = source.execute(
        "SELECT mal_id, title FROM catalog_ingestion_failures"
    ).fetchall()
    suspicious = [
        (mal_id, title)
        for mal_id, title in anime_rows + failure_rows
        if has_unexpected_synthetic_fixture_title(mal_id, title)
    ]
    if suspicious:
        details = ", ".join(
            f"MAL {mal_id}: {title!r}" for mal_id, title in suspicious
        )
        raise RuntimeError(
            "Refusing migration: unexpected synthetic fixture-like title(s) "
            f"require manual inspection: {details}"
        )

    contaminated_challenge_ids = set()
    for challenge_id, mal_id in source.execute(
        "SELECT challenge_id, mal_id FROM challenge_anime"
    ):
        if mal_id in KNOWN_SYNTHETIC_MAL_IDS:
            contaminated_challenge_ids.add(challenge_id)
    for challenge_id, anime_a_id, anime_b_id in source.execute(
        "SELECT challenge_id, anime_a_id, anime_b_id FROM matchup_history"
    ):
        if (
            anime_a_id in KNOWN_SYNTHETIC_MAL_IDS
            or anime_b_id in KNOWN_SYNTHETIC_MAL_IDS
        ):
            contaminated_challenge_ids.add(challenge_id)
    for challenge_id, selected_mal_id, correct_mal_id in source.execute(
        "SELECT challenge_id, selected_mal_id, correct_mal_id FROM player_answers"
    ):
        if (
            selected_mal_id in KNOWN_SYNTHETIC_MAL_IDS
            or correct_mal_id in KNOWN_SYNTHETIC_MAL_IDS
        ):
            contaminated_challenge_ids.add(challenge_id)

    challenge_rows = source.execute(
        "SELECT id, challenge_date FROM challenge_runs ORDER BY id"
    ).fetchall()
    contaminated_challenges = [
        {"id": challenge_id, "challenge_date": challenge_date}
        for challenge_id, challenge_date in challenge_rows
        if challenge_id in contaminated_challenge_ids
    ]
    source_counts = {}
    skipped_counts = {}
    for table in TABLES_IN_ORDER:
        rows = source.execute(
            f"SELECT {', '.join(TABLE_COLUMNS[table])} FROM {table}"
        ).fetchall()
        source_counts[table] = len(rows)
        skipped_counts[table] = sum(
            not _row_is_safe(table, row, contaminated_challenge_ids)
            for row in rows
        )

    return {
        "fixture_anime_count": sum(
            is_known_synthetic_fixture(mal_id, title)
            for mal_id, title in anime_rows
        ),
        "contaminated_challenge_ids": contaminated_challenge_ids,
        "contaminated_challenges": contaminated_challenges,
        "source_counts": source_counts,
        "skipped_counts": skipped_counts,
    }


def _print_preflight_report(plan):
    print("Synthetic fixture preflight")
    print(f"Known fixture anime found: {plan['fixture_anime_count']}")
    if plan["contaminated_challenges"]:
        contaminated = ", ".join(
            f"#{row['id']} ({row['challenge_date']})"
            for row in plan["contaminated_challenges"]
        )
        print(f"Contaminated challenges skipped: {contaminated}")
    else:
        print("Contaminated challenges skipped: none")
    affected = {
        table: count
        for table, count in plan["skipped_counts"].items()
        if count
    }
    print(
        "Affected rows skipped: "
        + (
            ", ".join(f"{table}={count}" for table, count in affected.items())
            or "none"
        )
    )


def _migration_rows(source, table, plan):
    columns = TABLE_COLUMNS[table]
    quoted_columns = ", ".join(columns)
    rows = source.execute(
        f"SELECT {quoted_columns} FROM {table}"
    ).fetchall()
    return [
        row for row in rows
        if _row_is_safe(table, row, plan["contaminated_challenge_ids"])
    ]


def _copy_table(source, destination, table, plan):
    columns = TABLE_COLUMNS[table]
    quoted_columns = ", ".join(columns)
    placeholders = ", ".join("?" for _ in columns)
    rows = _migration_rows(source, table, plan)
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


def _verify_migration(source, destination, expected_counts, plan):
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
        rows = _migration_rows(source, table, plan)
        key_index = TABLE_COLUMNS[table].index(key)
        source_value = min((row[key_index] for row in rows), default=None)
        destination_value = destination.execute(query).fetchone()
        if source_value != (
            destination_value[0] if destination_value else None
        ):
            raise RuntimeError(f"Representative {table} record verification failed.")

    orphan_checks = (
        "SELECT COUNT(*) FROM anime_relations r LEFT JOIN anime a ON a.mal_id = r.source_mal_id WHERE a.mal_id IS NULL",
        "SELECT COUNT(*) FROM challenge_anime ca LEFT JOIN challenge_runs c ON c.id = ca.challenge_id WHERE c.id IS NULL",
        "SELECT COUNT(*) FROM challenge_anime ca LEFT JOIN anime a ON a.mal_id = ca.mal_id WHERE a.mal_id IS NULL",
        "SELECT COUNT(*) FROM matchup_history mh LEFT JOIN challenge_runs c ON c.id = mh.challenge_id WHERE c.id IS NULL",
        "SELECT COUNT(*) FROM matchup_history mh LEFT JOIN anime a ON a.mal_id = mh.anime_a_id WHERE a.mal_id IS NULL",
        "SELECT COUNT(*) FROM matchup_history mh LEFT JOIN anime a ON a.mal_id = mh.anime_b_id WHERE a.mal_id IS NULL",
        "SELECT COUNT(*) FROM player_results pr LEFT JOIN challenge_runs c ON c.id = pr.challenge_id WHERE c.id IS NULL",
        "SELECT COUNT(*) FROM player_results pr LEFT JOIN players p ON p.id = pr.player_id WHERE p.id IS NULL",
        "SELECT COUNT(*) FROM player_answers pa LEFT JOIN players p ON p.id = pa.player_id WHERE p.id IS NULL",
        "SELECT COUNT(*) FROM player_answers pa LEFT JOIN challenge_runs c ON c.id = pa.challenge_id WHERE c.id IS NULL",
    )
    if any(destination.execute(query).fetchone()[0] for query in orphan_checks):
        raise RuntimeError("Foreign-key verification found orphaned rows.")


def migrate(source_path=DATABASE_PATH):
    if not os.getenv("DATABASE_URL"):
        raise RuntimeError("DATABASE_URL is required for SQLite-to-PostgreSQL migration.")

    source = _open_sqlite_read_only(source_path)
    destination = None

    try:
        plan = _scan_source(source)
        _print_preflight_report(plan)
        initialize_database()
        destination = _connect_database(DATABASE_PATH)
        _destination_is_empty(destination)
        counts = {}
        for table in TABLES_IN_ORDER:
            counts[table] = _copy_table(source, destination, table, plan)

        _verify_migration(source, destination, counts, plan)
        sequence_max = _reset_challenge_sequence(
            destination, counts["challenge_runs"]
        )
        destination.commit()
    except Exception:
        if destination is not None:
            destination.rollback()
        raise
    finally:
        source.close()
        if destination is not None:
            destination.close()

    return counts, sequence_max, plan


def main():
    counts, sequence_max, plan = migrate()
    print("SQLite → PostgreSQL migration complete")
    print("Table                       SQLite rows  Skipped  PostgreSQL rows")
    for table, count in counts.items():
        print(
            f"{table:28} {plan['source_counts'][table]:11}  "
            f"{plan['skipped_counts'][table]:7}  {count:15}"
        )
    print(f"challenge_runs sequence advanced through ID {sequence_max}")


if __name__ == "__main__":
    main()
