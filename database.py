import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


DATABASE_PATH = Path(__file__).parent / "data" / "anime_daily.db"

CREATE_ANIME_TABLE = """
CREATE TABLE IF NOT EXISTS anime (
    mal_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    image_url TEXT,
    score REAL,
    popularity_rank INTEGER,
    members INTEGER,
    entry_episodes INTEGER,
    series_episodes INTEGER,
    release_date TEXT,
    type TEXT,
    runtime_minutes INTEGER,
    relations_fetched INTEGER NOT NULL DEFAULT 0
)
"""

CREATE_INGESTION_TABLES = """
CREATE TABLE IF NOT EXISTS anime_relations (
    source_mal_id INTEGER NOT NULL,
    target_mal_id INTEGER NOT NULL,
    relation_type TEXT NOT NULL,
    PRIMARY KEY (source_mal_id, target_mal_id, relation_type),
    FOREIGN KEY (source_mal_id) REFERENCES anime(mal_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS anime_relations_target_index
ON anime_relations(target_mal_id);
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

CREATE UNIQUE INDEX IF NOT EXISTS challenge_runs_date_unique_index
ON challenge_runs(challenge_date);

CREATE INDEX IF NOT EXISTS challenge_runs_date_index
ON challenge_runs(challenge_date);

CREATE INDEX IF NOT EXISTS matchup_history_date_index
ON matchup_history(challenge_date);
"""

UPSERT_ANIME = """
INSERT INTO anime (
    mal_id,
    title,
    image_url,
    score,
    popularity_rank,
    members,
    entry_episodes,
    series_episodes,
    release_date,
    type,
    runtime_minutes
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(mal_id) DO UPDATE SET
    title = COALESCE(excluded.title, anime.title),
    image_url = COALESCE(excluded.image_url, anime.image_url),
    score = COALESCE(excluded.score, anime.score),
    popularity_rank = COALESCE(excluded.popularity_rank, anime.popularity_rank),
    members = COALESCE(excluded.members, anime.members),
    entry_episodes = COALESCE(excluded.entry_episodes, anime.entry_episodes),
    series_episodes = COALESCE(excluded.series_episodes, anime.series_episodes),
    release_date = COALESCE(excluded.release_date, anime.release_date),
    type = COALESCE(excluded.type, anime.type),
    runtime_minutes = COALESCE(excluded.runtime_minutes, anime.runtime_minutes)
"""

EPISODIC_MEDIA_TYPES = {"tv", "ona", "ova", "special", "tv_special"}
MAINLINE_RELATION_TYPES = {"prequel", "sequel"}


def _connect_database(database_path):
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _migrate_anime_table(connection):
    column_names = {
        column[1] for column in connection.execute("PRAGMA table_info(anime)")
    }

    if "image_url" not in column_names:
        connection.execute("ALTER TABLE anime ADD COLUMN image_url TEXT")

    if "relations_fetched" not in column_names:
        connection.execute(
            "ALTER TABLE anime "
            "ADD COLUMN relations_fetched INTEGER NOT NULL DEFAULT 0"
        )


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
        _migrate_anime_table(connection)
        connection.executescript(CREATE_INGESTION_TABLES)
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
            anime.get("image_url"),
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


def load_anime_ingestion_record(anime_id, database_path=DATABASE_PATH):
    initialize_database(database_path)
    connection = _connect_database(database_path)
    connection.row_factory = sqlite3.Row

    try:
        row = connection.execute(
            """
            SELECT
                mal_id,
                title,
                image_url,
                score,
                popularity_rank,
                members,
                entry_episodes,
                series_episodes,
                release_date,
                type,
                runtime_minutes,
                relations_fetched
            FROM anime
            WHERE mal_id = ?
            """,
            (anime_id,),
        ).fetchone()
    finally:
        connection.close()

    return dict(row) if row is not None else None


def store_anime_relations(anime_id, relations, database_path=DATABASE_PATH):
    """Replace one successfully fetched relationship list atomically.

    An empty list is meaningful: the fetched flag distinguishes it from a list
    that has never been fetched or whose request failed.
    """
    initialize_database(database_path)
    relation_rows = {
        (
            anime_id,
            relation.get("mal_id"),
            relation.get("relation_type"),
        )
        for relation in relations
        if relation.get("mal_id") is not None
        and relation.get("relation_type") is not None
    }
    connection = _connect_database(database_path)

    try:
        anime_state = connection.execute(
            "SELECT relations_fetched FROM anime WHERE mal_id = ?",
            (anime_id,),
        ).fetchone()

        if anime_state is None:
            raise ValueError(
                f"Cannot store relationships before anime {anime_id} is stored."
            )

        existing_rows = set(
            connection.execute(
                """
                SELECT source_mal_id, target_mal_id, relation_type
                FROM anime_relations
                WHERE source_mal_id = ?
                """,
                (anime_id,),
            ).fetchall()
        )

        if anime_state[0] and existing_rows == relation_rows:
            return len(relation_rows)

        connection.execute(
            "DELETE FROM anime_relations WHERE source_mal_id = ?",
            (anime_id,),
        )
        connection.executemany(
            """
            INSERT INTO anime_relations (
                source_mal_id,
                target_mal_id,
                relation_type
            )
            VALUES (?, ?, ?)
            """,
            relation_rows,
        )
        connection.execute(
            "UPDATE anime SET relations_fetched = 1 WHERE mal_id = ?",
            (anime_id,),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return len(relation_rows)


def load_anime_relations(anime_id, database_path=DATABASE_PATH):
    """Return None when relations are unresolved, including after a failure."""
    initialize_database(database_path)
    connection = _connect_database(database_path)
    connection.row_factory = sqlite3.Row

    try:
        anime = connection.execute(
            "SELECT relations_fetched FROM anime WHERE mal_id = ?",
            (anime_id,),
        ).fetchone()

        if anime is None or not anime["relations_fetched"]:
            return None

        rows = connection.execute(
            """
            SELECT target_mal_id AS mal_id, relation_type
            FROM anime_relations
            WHERE source_mal_id = ?
            ORDER BY target_mal_id, relation_type
            """,
            (anime_id,),
        ).fetchall()
    finally:
        connection.close()

    return [dict(row) for row in rows]


def load_mainline_neighbor_ids(anime_id, database_path=DATABASE_PATH):
    initialize_database(database_path)
    connection = _connect_database(database_path)
    placeholders = ", ".join("?" for _ in MAINLINE_RELATION_TYPES)
    relation_types = tuple(sorted(MAINLINE_RELATION_TYPES))

    try:
        rows = connection.execute(
            f"""
            SELECT target_mal_id
            FROM anime_relations
            WHERE source_mal_id = ?
              AND relation_type IN ({placeholders})
            UNION
            SELECT source_mal_id
            FROM anime_relations
            WHERE target_mal_id = ?
              AND relation_type IN ({placeholders})
            """,
            (anime_id, *relation_types, anime_id, *relation_types),
        ).fetchall()
    finally:
        connection.close()

    return {row[0] for row in rows}


def resolve_and_store_series_episode_count(
    anime_id,
    database_path=DATABASE_PATH,
):
    """Resolve a complete stored mainline component and cache its shared total."""
    initialize_database(database_path)
    connection = _connect_database(database_path)
    connection.row_factory = sqlite3.Row
    anime_ids_to_visit = [anime_id]
    visited_anime_ids = set()
    episode_total = 0
    relation_placeholders = ", ".join("?" for _ in MAINLINE_RELATION_TYPES)
    relation_types = tuple(sorted(MAINLINE_RELATION_TYPES))

    try:
        while anime_ids_to_visit:
            current_anime_id = anime_ids_to_visit.pop()

            if current_anime_id in visited_anime_ids:
                continue

            anime = connection.execute(
                """
                SELECT mal_id, type, entry_episodes, relations_fetched
                FROM anime
                WHERE mal_id = ?
                """,
                (current_anime_id,),
            ).fetchone()

            if anime is None or not anime["relations_fetched"]:
                return None

            visited_anime_ids.add(current_anime_id)

            if anime["type"] in EPISODIC_MEDIA_TYPES:
                entry_episodes = anime["entry_episodes"]

                if entry_episodes is not None:
                    episode_total += entry_episodes

            neighbor_rows = connection.execute(
                f"""
                SELECT target_mal_id AS mal_id
                FROM anime_relations
                WHERE source_mal_id = ?
                  AND relation_type IN ({relation_placeholders})
                UNION
                SELECT source_mal_id AS mal_id
                FROM anime_relations
                WHERE target_mal_id = ?
                  AND relation_type IN ({relation_placeholders})
                """,
                (
                    current_anime_id,
                    *relation_types,
                    current_anime_id,
                    *relation_types,
                ),
            ).fetchall()

            for row in neighbor_rows:
                if row["mal_id"] not in visited_anime_ids:
                    anime_ids_to_visit.append(row["mal_id"])

        placeholders = ", ".join("?" for _ in visited_anime_ids)
        connection.execute(
            f"""
            UPDATE anime
            SET series_episodes = ?
            WHERE mal_id IN ({placeholders})
            """,
            (episode_total, *visited_anime_ids),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return episode_total


def load_ingestion_summary(database_path=DATABASE_PATH):
    initialize_database(database_path)
    connection = _connect_database(database_path)

    try:
        total_anime = connection.execute("SELECT COUNT(*) FROM anime").fetchone()[0]
        relationship_rows = connection.execute(
            "SELECT COUNT(*) FROM anime_relations"
        ).fetchone()[0]
        resolved_series = connection.execute(
            "SELECT COUNT(*) FROM anime WHERE series_episodes IS NOT NULL"
        ).fetchone()[0]
        missing_series = total_anime - resolved_series
        media_type_counts = dict(
            connection.execute(
                """
                SELECT COALESCE(type, 'unknown'), COUNT(*)
                FROM anime
                GROUP BY COALESCE(type, 'unknown')
                """
            ).fetchall()
        )
    finally:
        connection.close()

    return {
        "total_anime": total_anime,
        "relationship_rows": relationship_rows,
        "resolved_series": resolved_series,
        "missing_series": missing_series,
        "media_type_counts": media_type_counts,
    }


def load_anime_records(database_path=DATABASE_PATH):
    database_path = Path(database_path)

    if not database_path.exists():
        raise RuntimeError(
            f"Anime database not found at {database_path}. Run import_catalog.py first."
        )

    initialize_database(database_path)
    connection = _connect_database(database_path)
    connection.row_factory = sqlite3.Row

    try:
        rows = connection.execute(
            """
            SELECT
                mal_id,
                title,
                image_url,
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


def load_challenge_record(challenge_date, database_path=DATABASE_PATH):
    challenge_date = _parse_challenge_date(challenge_date).isoformat()
    initialize_database(database_path)
    connection = _connect_database(database_path)
    connection.row_factory = sqlite3.Row

    try:
        challenge_run = connection.execute(
            """
            SELECT id, challenge_date, created_at
            FROM challenge_runs
            WHERE challenge_date = ?
            """,
            (challenge_date,),
        ).fetchone()

        if challenge_run is None:
            return None

        placements = connection.execute(
            """
            SELECT
                challenge_anime.category,
                challenge_anime.position,
                anime.mal_id,
                anime.title,
                anime.image_url,
                anime.score,
                anime.popularity_rank,
                anime.members,
                anime.entry_episodes,
                anime.series_episodes,
                anime.release_date,
                anime.type,
                anime.runtime_minutes
            FROM challenge_anime
            JOIN anime ON anime.mal_id = challenge_anime.mal_id
            WHERE challenge_anime.challenge_id = ?
            ORDER BY challenge_anime.category, challenge_anime.position
            """,
            (challenge_run["id"],),
        ).fetchall()
    finally:
        connection.close()

    return {
        "id": challenge_run["id"],
        "challenge_date": challenge_run["challenge_date"],
        "created_at": challenge_run["created_at"],
        "placements": [dict(placement) for placement in placements],
    }


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
