import os
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:
    import psycopg
except ImportError:  # pragma: no cover - SQLite-only environments
    psycopg = None


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

CREATE TABLE IF NOT EXISTS catalog_ingestion_failures (
    mal_id INTEGER PRIMARY KEY,
    title TEXT,
    stage TEXT NOT NULL,
    reason TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingestion_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

CREATE_HISTORY_TABLES = """
CREATE TABLE IF NOT EXISTS challenge_runs (
    id INTEGER PRIMARY KEY,
    challenge_date TEXT NOT NULL,
    created_at TEXT NOT NULL,
    publication_state TEXT NOT NULL DEFAULT 'approved'
        CHECK (publication_state IN ('draft', 'approved'))
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

CREATE_PLAYER_TABLES = """
CREATE TABLE IF NOT EXISTS players (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);

-- Retain the historical 25-point ceiling for existing five-round results.
-- New results are additionally checked against their stored matchup count.
CREATE TABLE IF NOT EXISTS player_results (
    player_id TEXT NOT NULL,
    challenge_id INTEGER NOT NULL,
    score INTEGER NOT NULL CHECK (score BETWEEN 0 AND 25),
    completed_at TEXT NOT NULL,
    PRIMARY KEY (player_id, challenge_id),
    FOREIGN KEY (player_id) REFERENCES players(id) ON DELETE CASCADE,
    FOREIGN KEY (challenge_id) REFERENCES challenge_runs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS player_results_challenge_index
ON player_results(challenge_id);

CREATE TABLE IF NOT EXISTS player_answers (
    player_id TEXT NOT NULL,
    challenge_id INTEGER NOT NULL,
    category TEXT NOT NULL,
    comparison_position INTEGER NOT NULL CHECK (comparison_position BETWEEN 1 AND 5),
    selected_mal_id INTEGER NOT NULL,
    correct_mal_id INTEGER NOT NULL,
    correct INTEGER NOT NULL CHECK (correct IN (0, 1)),
    answered_at TEXT NOT NULL,
    PRIMARY KEY (player_id, challenge_id, category, comparison_position),
    FOREIGN KEY (player_id) REFERENCES players(id) ON DELETE CASCADE,
    FOREIGN KEY (challenge_id) REFERENCES challenge_runs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS player_answers_challenge_index
ON player_answers(player_id, challenge_id);
"""

POSTGRES_CHALLENGE_MIGRATIONS = (
    "ALTER TABLE challenge_runs ADD COLUMN IF NOT EXISTS publication_state TEXT NOT NULL DEFAULT 'approved' CHECK (publication_state IN ('draft', 'approved'))",
)

POSTGRES_ANIME_MIGRATIONS = (
    "ALTER TABLE anime ADD COLUMN IF NOT EXISTS image_url TEXT",
    "ALTER TABLE anime ADD COLUMN IF NOT EXISTS relations_fetched INTEGER NOT NULL DEFAULT 0",
)

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
DERIVATIVE_CHILD_RELATION_TYPES = {"parent_story", "full_story"}
FAILURE_STATE_KEY = "catalog_failures_initialized"
_SERIES_REPRESENTATIVE_CACHE = {}
_POSTGRES_INITIALIZED_TARGETS = set()


class _HybridRow(dict):
    """A small row compatible with both SQLite's key and index access."""

    def __getitem__(self, key):
        if isinstance(key, int):
            return tuple(self.values())[key]
        return super().__getitem__(key)


def _postgres_enabled(database_path):
    configured_url = os.getenv("DATABASE_URL")
    if not configured_url or psycopg is None:
        return False
    # Explicit temporary/alternate paths remain SQLite during migration.
    return Path(database_path).resolve() == DATABASE_PATH.resolve()


def _series_representative_cache_key(database_path):
    if _postgres_enabled(database_path):
        return "postgres", os.environ["DATABASE_URL"]
    return "sqlite", str(Path(database_path).resolve())


def _invalidate_series_representative_cache(database_path):
    _SERIES_REPRESENTATIVE_CACHE.pop(
        _series_representative_cache_key(database_path),
        None,
    )


def _replace_placeholders(sql):
    """Convert SQLite placeholders outside quoted SQL strings for psycopg."""
    result = []
    quote = None
    for character in sql:
        if character in ("'", '"'):
            if quote == character:
                quote = None
            elif quote is None:
                quote = character
        if character == "?" and quote is None:
            result.append("%s")
        else:
            result.append(character)
    return "".join(result)


def _adapt_postgres_sql(sql):
    sql = _replace_placeholders(sql)
    if re.search(r"INSERT\s+OR\s+IGNORE\s+INTO", sql, re.IGNORECASE):
        sql = re.sub(
            r"INSERT\s+OR\s+IGNORE\s+INTO",
            "INSERT INTO",
            sql,
            flags=re.IGNORECASE,
        )
        sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    return sql


class _PostgresCursor:
    def __init__(self, cursor):
        self._cursor = cursor
        self.rowcount = cursor.rowcount

    def _row(self, row):
        if row is None:
            return None
        columns = [column.name for column in self._cursor.description]
        return _HybridRow(zip(columns, row))

    def fetchone(self):
        return self._row(self._cursor.fetchone())

    def fetchall(self):
        return [self._row(row) for row in self._cursor.fetchall()]


class _PostgresConnection:
    is_postgres = True

    def __init__(self, url):
        self._connection = psycopg.connect(url)
        self.row_factory = None

    def execute(self, sql, parameters=()):
        cursor = self._connection.execute(_adapt_postgres_sql(sql), parameters)
        return _PostgresCursor(cursor)

    def executemany(self, sql, parameter_rows):
        cursor = self._connection.cursor()
        cursor.executemany(_adapt_postgres_sql(sql), parameter_rows)
        return _PostgresCursor(cursor)

    def executescript(self, script):
        for statement in script.split(";"):
            if statement.strip():
                self.execute(statement)

    def commit(self):
        self._connection.commit()

    def rollback(self):
        self._connection.rollback()

    def close(self):
        self._connection.close()


def _connect_database(database_path):
    if _postgres_enabled(database_path):
        return _PostgresConnection(os.environ["DATABASE_URL"])

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


def _initialize_postgres(connection):
    # The CREATE statements are portable; PostgreSQL needs a serial identity
    # for challenge IDs and explicit ALTER statements for existing databases.
    connection.execute(CREATE_ANIME_TABLE)
    for statement in POSTGRES_ANIME_MIGRATIONS:
        connection.execute(statement)
    connection.executescript(CREATE_INGESTION_TABLES)
    connection.executescript(CREATE_HISTORY_TABLES.replace(
        "id INTEGER PRIMARY KEY", "id BIGSERIAL PRIMARY KEY", 1
    ))
    for statement in POSTGRES_CHALLENGE_MIGRATIONS:
        connection.execute(statement)
    connection.executescript(CREATE_PLAYER_TABLES)


def _initialize_sqlite(connection):
    connection.execute(CREATE_ANIME_TABLE)
    _migrate_anime_table(connection)
    connection.executescript(CREATE_INGESTION_TABLES)
    connection.executescript(CREATE_HISTORY_TABLES)
    column_names = {
        column[1] for column in connection.execute("PRAGMA table_info(challenge_runs)")
    }
    if "publication_state" not in column_names:
        connection.execute(
            "ALTER TABLE challenge_runs ADD COLUMN publication_state TEXT NOT NULL DEFAULT 'approved'"
        )
    connection.executescript(CREATE_PLAYER_TABLES)


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
    if _postgres_enabled(database_path):
        cache_key = _series_representative_cache_key(database_path)
        if cache_key in _POSTGRES_INITIALIZED_TARGETS:
            return

        connection = _connect_database(database_path)
        try:
            _initialize_postgres(connection)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        _POSTGRES_INITIALIZED_TARGETS.add(cache_key)
        return

    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = _connect_database(database_path)
    try:
        _initialize_sqlite(connection)
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

    _invalidate_series_representative_cache(database_path)
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

    _invalidate_series_representative_cache(database_path)
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


def load_series_representatives(anime_ids, database_path=DATABASE_PATH):
    """Find the first-released mainline episodic entry in each verified series."""
    anime_ids = list(dict.fromkeys(anime_ids))

    if not anime_ids:
        return {}

    def release_order_key(value):
        parts = str(value or "").split("-")
        if not 1 <= len(parts) <= 3 or any(not part.isdigit() for part in parts):
            return None
        year = int(parts[0])
        month = int(parts[1]) if len(parts) >= 2 else 1
        day = int(parts[2]) if len(parts) == 3 else 1
        try:
            date(year, month, day)
        except ValueError:
            return None
        return year, month, day

    cache_key = _series_representative_cache_key(database_path)
    if cache_key not in _SERIES_REPRESENTATIVE_CACHE:
        initialize_database(database_path)
        connection = _connect_database(database_path)
        connection.row_factory = sqlite3.Row
        relevant_relation_types = tuple(
            sorted(MAINLINE_RELATION_TYPES | DERIVATIVE_CHILD_RELATION_TYPES)
        )
        placeholders = ", ".join("?" for _ in relevant_relation_types)

        try:
            anime_rows = connection.execute(
                """
                SELECT mal_id, title, image_url, type, release_date,
                       relations_fetched
                FROM anime
                """
            ).fetchall()
            relation_rows = connection.execute(
                f"""
                SELECT source_mal_id, target_mal_id, relation_type
                FROM anime_relations
                WHERE relation_type IN ({placeholders})
                """,
                relevant_relation_types,
            ).fetchall()
        finally:
            connection.close()

        anime_by_id = {row["mal_id"]: row for row in anime_rows}
        derivative_children = set()
        neighbors = {}
        relations_by_member = {}

        for relation in relation_rows:
            source_id = relation["source_mal_id"]
            target_id = relation["target_mal_id"]
            relation_type = relation["relation_type"]

            if relation_type in DERIVATIVE_CHILD_RELATION_TYPES:
                derivative_children.add(source_id)
                continue

            relation = (source_id, target_id, relation_type)
            relations_by_member.setdefault(source_id, set()).add(relation)
            relations_by_member.setdefault(target_id, set()).add(relation)
            neighbors.setdefault(source_id, set()).add(target_id)
            neighbors.setdefault(target_id, set()).add(source_id)

        resolved_representatives = {}
        graph_ids = set(anime_by_id) | set(neighbors)

        for anime_id in graph_ids:
            if anime_id in resolved_representatives:
                continue

            to_visit = [anime_id]
            component = set()

            while to_visit:
                current_id = to_visit.pop()
                if current_id in component:
                    continue
                component.add(current_id)
                to_visit.extend(neighbors.get(current_id, set()) - component)

            representative = None
            complete = all(
                member_id in anime_by_id
                and anime_by_id[member_id]["relations_fetched"]
                for member_id in component
            )

            if complete:
                predecessors = {member_id: set() for member_id in component}
                successors = {member_id: set() for member_id in component}
                component_relations = set().union(
                    *(
                        relations_by_member.get(member_id, set())
                        for member_id in component
                    )
                )

                for source_id, target_id, relation_type in component_relations:
                    if relation_type == "prequel":
                        earlier_id, later_id = target_id, source_id
                    else:
                        earlier_id, later_id = source_id, target_id
                    predecessors[later_id].add(earlier_id)
                    successors[earlier_id].add(later_id)

                remaining_predecessors = {
                    member_id: len(earlier_ids)
                    for member_id, earlier_ids in predecessors.items()
                }
                ready = [
                    member_id
                    for member_id, earlier_count in remaining_predecessors.items()
                    if earlier_count == 0
                ]
                processed = 0

                while ready:
                    current_id = ready.pop()
                    processed += 1
                    for later_id in successors[current_id]:
                        remaining_predecessors[later_id] -= 1
                        if remaining_predecessors[later_id] == 0:
                            ready.append(later_id)

                release_order = []
                for member_id in component:
                    anime = anime_by_id[member_id]
                    if (
                        anime["type"] not in EPISODIC_MEDIA_TYPES
                        or member_id in derivative_children
                    ):
                        continue
                    released = release_order_key(anime["release_date"])
                    if released is None:
                        complete = False
                        break
                    release_order.append((released, member_id))

                if complete and processed == len(component) and release_order:
                    _, representative_id = min(release_order)
                    representative_anime = anime_by_id[representative_id]
                    representative = {
                        "mal_id": representative_anime["mal_id"],
                        "title": representative_anime["title"],
                        "image_url": representative_anime["image_url"],
                    }

            for member_id in component:
                resolved_representatives[member_id] = representative

        _SERIES_REPRESENTATIVE_CACHE[cache_key] = resolved_representatives

    resolved_representatives = _SERIES_REPRESENTATIVE_CACHE[cache_key]

    return {
        anime_id: resolved_representatives.get(anime_id)
        for anime_id in anime_ids
    }


def load_series_display_roots(anime_ids, database_path=DATABASE_PATH):
    """Backward-compatible alias for first-release series representatives."""
    return load_series_representatives(anime_ids, database_path)


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


def initialize_ingestion_failure_state(
    fallback_failures,
    database_path=DATABASE_PATH,
):
    """Seed SQLite once from the legacy JSON failure report."""
    initialize_database(database_path)
    connection = _connect_database(database_path)

    try:
        state = connection.execute(
            "SELECT value FROM ingestion_state WHERE key = ?",
            (FAILURE_STATE_KEY,),
        ).fetchone()

        if state is not None:
            return False

        updated_at = datetime.now(timezone.utc).isoformat()
        rows_by_id = {}

        for failure in fallback_failures:
            mal_id = failure.get("mal_id")

            if mal_id is None:
                continue

            rows_by_id[mal_id] = (
                mal_id,
                failure.get("title"),
                failure.get("stage") or "catalog_build",
                failure.get("reason") or "Unknown ingestion failure.",
                updated_at,
            )

        connection.executemany(
            """
            INSERT INTO catalog_ingestion_failures (
                mal_id,
                title,
                stage,
                reason,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(mal_id) DO UPDATE SET
                title = excluded.title,
                stage = excluded.stage,
                reason = excluded.reason,
                updated_at = excluded.updated_at
            """,
            rows_by_id.values(),
        )
        connection.execute(
            "INSERT INTO ingestion_state (key, value) VALUES (?, ?)",
            (FAILURE_STATE_KEY, "1"),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return True


def upsert_ingestion_failure(failure, database_path=DATABASE_PATH):
    mal_id = failure.get("mal_id")

    if mal_id is None:
        return False

    initialize_database(database_path)
    connection = _connect_database(database_path)

    try:
        connection.execute(
            """
            INSERT INTO catalog_ingestion_failures (
                mal_id,
                title,
                stage,
                reason,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(mal_id) DO UPDATE SET
                title = excluded.title,
                stage = excluded.stage,
                reason = excluded.reason,
                updated_at = excluded.updated_at
            """,
            (
                mal_id,
                failure.get("title"),
                failure.get("stage") or "catalog_build",
                failure.get("reason") or "Unknown ingestion failure.",
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return True


def remove_ingestion_failure(anime_id, database_path=DATABASE_PATH):
    initialize_database(database_path)
    connection = _connect_database(database_path)

    try:
        cursor = connection.execute(
            "DELETE FROM catalog_ingestion_failures WHERE mal_id = ?",
            (anime_id,),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return cursor.rowcount > 0


def load_ingestion_failures(database_path=DATABASE_PATH):
    initialize_database(database_path)
    connection = _connect_database(database_path)
    connection.row_factory = sqlite3.Row

    try:
        rows = connection.execute(
            """
            SELECT mal_id, title, stage, reason, updated_at
            FROM catalog_ingestion_failures
            ORDER BY mal_id
            """
        ).fetchall()
    finally:
        connection.close()

    return [dict(row) for row in rows]


def load_anime_records(database_path=DATABASE_PATH):
    database_path = Path(database_path)

    if not _postgres_enabled(database_path) and not database_path.exists():
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


def load_recent_category_anime_ids(
    challenge_date,
    recent_days,
    database_path=DATABASE_PATH,
):
    """Load exact MAL IDs used in each category during prior cooldown days."""
    challenge_date = _parse_challenge_date(challenge_date)
    earliest_date = (challenge_date - timedelta(days=recent_days)).isoformat()
    latest_date = challenge_date.isoformat()
    initialize_database(database_path)
    connection = _connect_database(database_path)
    connection.row_factory = sqlite3.Row

    try:
        rows = connection.execute(
            """
            SELECT DISTINCT challenge_anime.category, challenge_anime.mal_id
            FROM challenge_anime
            JOIN challenge_runs
                ON challenge_runs.id = challenge_anime.challenge_id
            WHERE challenge_runs.challenge_date >= ?
              AND challenge_runs.challenge_date < ?
            """,
            (earliest_date, latest_date),
        ).fetchall()
    finally:
        connection.close()

    recent_by_category = {}
    for row in rows:
        recent_by_category.setdefault(row["category"], set()).add(row["mal_id"])
    return recent_by_category


def load_challenge_record(
    challenge_date,
    database_path=DATABASE_PATH,
    include_drafts=False,
):
    challenge_date = _parse_challenge_date(challenge_date).isoformat()
    initialize_database(database_path)
    connection = _connect_database(database_path)
    connection.row_factory = sqlite3.Row

    try:
        challenge_run = connection.execute(
            """
            SELECT id, challenge_date, created_at, publication_state
            FROM challenge_runs
            WHERE challenge_date = ?
              AND (? OR publication_state = 'approved')
            """,
            (challenge_date, include_drafts),
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
        "publication_state": challenge_run["publication_state"],
        "placements": [dict(placement) for placement in placements],
    }


def load_recent_challenge_dates(
    target_challenge_date,
    database_path=DATABASE_PATH,
    count=7,
):
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise ValueError("count must be a non-negative integer.")

    if count == 0:
        return []

    target_challenge_date = _parse_challenge_date(
        target_challenge_date
    ).isoformat()
    initialize_database(database_path)
    connection = _connect_database(database_path)

    try:
        rows = connection.execute(
            """
            SELECT challenge_date
            FROM challenge_runs
            WHERE challenge_date < ?
            ORDER BY challenge_date DESC
            LIMIT ?
            """,
            (target_challenge_date, count),
        ).fetchall()
    finally:
        connection.close()

    return [row[0] for row in reversed(rows)]


def list_challenge_runs(database_path=DATABASE_PATH):
    initialize_database(database_path)
    connection = _connect_database(database_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT id, challenge_date, created_at, publication_state
            FROM challenge_runs
            ORDER BY challenge_date
            """
        ).fetchall()
    finally:
        connection.close()
    return [dict(row) for row in rows]


def set_challenge_publication_state(
    challenge_date,
    publication_state,
    database_path=DATABASE_PATH,
):
    if publication_state not in {"draft", "approved"}:
        raise ValueError("publication_state must be 'draft' or 'approved'.")
    normalized_date = _parse_challenge_date(challenge_date).isoformat()
    initialize_database(database_path)
    connection = _connect_database(database_path)
    try:
        cursor = connection.execute(
            """
            UPDATE challenge_runs
            SET publication_state = ?
            WHERE challenge_date = ?
              AND publication_state = 'draft'
            """,
            (publication_state, normalized_date),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return cursor.rowcount > 0


def delete_draft_challenge(challenge_date, database_path=DATABASE_PATH):
    normalized_date = _parse_challenge_date(challenge_date).isoformat()
    initialize_database(database_path)
    connection = _connect_database(database_path)
    try:
        cursor = connection.execute(
            """
            DELETE FROM challenge_runs
            WHERE challenge_date = ? AND publication_state = 'draft'
            """,
            (normalized_date,),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return cursor.rowcount > 0


def _load_challenge_player_activity_from_connection(connection, challenge_id):
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM player_answers WHERE challenge_id = ?) AS answer_rows,
            (SELECT COUNT(*) FROM player_results WHERE challenge_id = ?) AS result_rows,
            (SELECT COUNT(DISTINCT player_id) FROM player_answers
             WHERE challenge_id = ?) AS answer_players,
            (SELECT COUNT(DISTINCT player_id) FROM player_results
             WHERE challenge_id = ?) AS result_players
        """,
        (challenge_id, challenge_id, challenge_id, challenge_id),
    ).fetchone()
    return dict(row)


def load_challenge_player_activity(challenge_id, database_path=DATABASE_PATH):
    """Return official answer/result counts for a challenge."""
    initialize_database(database_path)
    connection = _connect_database(database_path)
    try:
        return _load_challenge_player_activity_from_connection(connection, challenge_id)
    finally:
        connection.close()


def delete_challenge(
    challenge_date,
    database_path=DATABASE_PATH,
    allow_approved=False,
):
    """Delete one challenge only after enforcing repair safety checks."""
    normalized_date = _parse_challenge_date(challenge_date).isoformat()
    initialize_database(database_path)
    connection = _connect_database(database_path)
    connection.row_factory = sqlite3.Row
    try:
        query = """
            SELECT id, publication_state
            FROM challenge_runs
            WHERE challenge_date = ?
        """
        if getattr(connection, "is_postgres", False):
            query += " FOR UPDATE"
        record = connection.execute(query, (normalized_date,)).fetchone()
        if record is None:
            return False
        if record["publication_state"] == "approved" and not allow_approved:
            raise ValueError("Approved challenges require explicit repair authorization.")

        activity = _load_challenge_player_activity_from_connection(
            connection, record["id"]
        )
        if activity["answer_rows"] or activity["result_rows"]:
            raise ValueError(
                "Challenge has official player answers/results; manual intervention is required."
            )

        connection.execute(
            "DELETE FROM challenge_runs WHERE id = ?",
            (record["id"],),
        )
        connection.commit()
        return True
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


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
    publication_state="approved",
):
    if len(challenge) != 4 or any(
        len(category.get("anime", [])) != 6 for category in challenge
    ):
        raise ValueError(
            "A complete challenge must contain four categories with six anime each."
        )

    if publication_state not in {"draft", "approved"}:
        raise ValueError("publication_state must be 'draft' or 'approved'.")

    challenge_date = _parse_challenge_date(challenge_date).isoformat()

    if created_at is None:
        created_at = datetime.now(timezone.utc).isoformat()

    if Path(database_path).resolve() == DATABASE_PATH.resolve() or _postgres_enabled(database_path):
        from challenge import contains_known_synthetic_fixture

        if contains_known_synthetic_fixture(challenge):
            raise ValueError(
                "Synthetic fixture challenges may only be written to a temporary test database."
            )

    if publication_state == "approved":
        # Keep direct database callers subject to the same publication gate as
        # the API and the developer approval workflow. Drafts remain available
        # for inspection and are validated when they are approved.
        from challenge import validate_public_challenge

        validate_public_challenge(challenge, challenge_date, database_path)

    initialize_database(database_path)
    connection = _connect_database(database_path)

    try:
        if getattr(connection, "is_postgres", False):
            cursor = connection.execute(
                """
                INSERT INTO challenge_runs (
                    challenge_date, created_at, publication_state
                )
                VALUES (?, ?, ?)
                RETURNING id
                """,
                (challenge_date, created_at, publication_state),
            )
            challenge_id = cursor.fetchone()[0]
        else:
            cursor = connection.execute(
                """
                INSERT INTO challenge_runs (
                    challenge_date, created_at, publication_state
                )
                VALUES (?, ?, ?)
                """,
                (challenge_date, created_at, publication_state),
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


def ensure_player(player_id, database_path=DATABASE_PATH, created_at=None):
    if created_at is None:
        created_at = datetime.now(timezone.utc).isoformat()

    initialize_database(database_path)
    connection = _connect_database(database_path)

    try:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO players (id, created_at)
            VALUES (?, ?)
            """,
            (player_id, created_at),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return cursor.rowcount == 1


def record_player_result(
    player_id,
    challenge_id,
    score,
    database_path=DATABASE_PATH,
    completed_at=None,
):
    if (
        not isinstance(score, int)
        or isinstance(score, bool)
        or score < 0
    ):
        raise ValueError("score must be a nonnegative integer.")

    if completed_at is None:
        completed_at = datetime.now(timezone.utc).isoformat()

    initialize_database(database_path)
    connection = _connect_database(database_path)
    connection.row_factory = sqlite3.Row

    try:
        question_count = connection.execute(
            "SELECT COUNT(*) FROM matchup_history WHERE challenge_id = ?",
            (challenge_id,),
        ).fetchone()[0]
        if not 0 <= score <= question_count:
            raise ValueError(f"score must be between 0 and {question_count}.")

        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO player_results (
                player_id,
                challenge_id,
                score,
                completed_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (player_id, challenge_id, score, completed_at),
        )
        first_completion = cursor.rowcount == 1
        official_result = connection.execute(
            """
            SELECT player_id, challenge_id, score, completed_at
            FROM player_results
            WHERE player_id = ? AND challenge_id = ?
            """,
            (player_id, challenge_id),
        ).fetchone()
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    result = dict(official_result)
    result["first_completion"] = first_completion
    return result


def load_player_results(player_id, database_path=DATABASE_PATH):
    initialize_database(database_path)
    connection = _connect_database(database_path)
    connection.row_factory = sqlite3.Row

    try:
        rows = connection.execute(
            """
            SELECT
                challenge_runs.challenge_date,
                player_results.score,
                player_results.completed_at,
                (SELECT COUNT(*) FROM matchup_history
                 WHERE challenge_id = challenge_runs.id) AS total_questions
            FROM player_results
            JOIN challenge_runs
                ON challenge_runs.id = player_results.challenge_id
            WHERE player_results.player_id = ?
            ORDER BY challenge_runs.challenge_date DESC
            """,
            (player_id,),
        ).fetchall()
    finally:
        connection.close()

    return [dict(row) for row in rows]


def record_player_answer(
    player_id,
    challenge_id,
    category,
    comparison_position,
    selected_mal_id,
    correct_mal_id,
    correct,
    database_path=DATABASE_PATH,
    answered_at=None,
):
    if answered_at is None:
        answered_at = datetime.now(timezone.utc).isoformat()

    initialize_database(database_path)
    connection = _connect_database(database_path)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute(
            """
            INSERT OR IGNORE INTO player_answers (
                player_id, challenge_id, category, comparison_position,
                selected_mal_id, correct_mal_id, correct, answered_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                player_id, challenge_id, category, comparison_position,
                selected_mal_id, correct_mal_id, int(correct), answered_at,
            ),
        )
        row = connection.execute(
            """
            SELECT player_id, challenge_id, category, comparison_position,
                   selected_mal_id, correct_mal_id, correct, answered_at
            FROM player_answers
            WHERE player_id = ? AND challenge_id = ? AND category = ?
              AND comparison_position = ?
            """,
            (player_id, challenge_id, category, comparison_position),
        ).fetchone()
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return dict(row)


def load_player_answers(player_id, challenge_id, database_path=DATABASE_PATH):
    initialize_database(database_path)
    connection = _connect_database(database_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT player_id, challenge_id, category, comparison_position,
                   selected_mal_id, correct_mal_id, correct, answered_at
            FROM player_answers
            WHERE player_id = ? AND challenge_id = ?
            ORDER BY category, comparison_position
            """,
            (player_id, challenge_id),
        ).fetchall()
    finally:
        connection.close()
    return [dict(row) for row in rows]


def load_comparison_answer_stats(challenge_id, database_path=DATABASE_PATH):
    """Return anonymized official-answer aggregates for one challenge."""
    initialize_database(database_path)
    connection = _connect_database(database_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT category, comparison_position,
                   COUNT(*) AS total_answers,
                   SUM(correct) AS correct_answers
            FROM player_answers
            WHERE challenge_id = ?
            GROUP BY category, comparison_position
            ORDER BY category, comparison_position
            """,
            (challenge_id,),
        ).fetchall()
    finally:
        connection.close()

    stats = []
    for row in rows:
        total = int(row["total_answers"])
        correct = int(row["correct_answers"] or 0)
        stats.append(
            {
                "category": row["category"],
                "comparison_position": row["comparison_position"],
                "total_answers": total,
                "correct_answers": correct,
                "percentage": round(correct / total * 100, 1) if total else None,
            }
        )
    return stats


def load_player_result_standing(player_id, challenge_id, score, database_path=DATABASE_PATH):
    """Return a deterministic top-percent standing among official completions."""
    initialize_database(database_path)
    connection = _connect_database(database_path)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            """
            SELECT COUNT(*) AS completed_players,
                   SUM(CASE WHEN score > ? THEN 1 ELSE 0 END) AS higher_scores
            FROM player_results
            WHERE challenge_id = ?
            """,
            (score, challenge_id),
        ).fetchone()
    finally:
        connection.close()

    completed_players = int(row["completed_players"] or 0)
    higher_scores = int(row["higher_scores"] or 0)
    rank = 1 + higher_scores
    top_percent = (
        max(1, round(rank / completed_players * 100))
        if completed_players
        else None
    )
    return {
        "completed_players": completed_players,
        "rank": rank if completed_players else None,
        "top_percent": top_percent,
    }


def load_month_archive(
    player_id,
    first_date,
    next_month_date,
    database_path=DATABASE_PATH,
    minimum_date=None,
):
    initialize_database(database_path)
    connection = _connect_database(database_path)
    connection.row_factory = sqlite3.Row

    try:
        query = """
            SELECT
                challenge_runs.challenge_date,
                player_results.score AS official_score,
                player_results.completed_at,
                (SELECT COUNT(*) FROM matchup_history
                 WHERE challenge_id = challenge_runs.id) AS total_questions,
                (SELECT COUNT(DISTINCT category) FROM challenge_anime
                 WHERE challenge_id = challenge_runs.id) AS category_count
            FROM challenge_runs
            LEFT JOIN player_results
                ON player_results.challenge_id = challenge_runs.id
                AND player_results.player_id = ?
            WHERE challenge_runs.challenge_date >= ?
                AND challenge_runs.challenge_date < ?
                AND challenge_runs.publication_state = 'approved'
        """
        parameters = [player_id, str(first_date), str(next_month_date)]
        if minimum_date is not None:
            query += " AND challenge_runs.challenge_date >= ?\n"
            parameters.append(str(minimum_date))
        query += " ORDER BY challenge_runs.challenge_date"
        rows = connection.execute(query, parameters).fetchall()
    finally:
        connection.close()

    return [dict(row) for row in rows]
