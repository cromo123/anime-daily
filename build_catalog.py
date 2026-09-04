import argparse
import json
import os
import time
from pathlib import Path

from database import (
    DATABASE_PATH,
    initialize_database,
    initialize_ingestion_failure_state,
    load_anime_ingestion_record,
    load_ingestion_failures,
    load_anime_records,
    load_anime_relations,
    load_ingestion_summary,
    load_mainline_neighbor_ids,
    resolve_and_store_series_episode_count,
    remove_ingestion_failure,
    store_anime_relations,
    upsert_ingestion_failure,
    upsert_anime_records,
)
from mal_client import (
    fetch_anime,
    fetch_anime_ranking,
    fetch_related_anime,
    get_mal_api_request_count,
)


POPULAR_CANDIDATE_TARGET = 350
MOVIE_CANDIDATE_TARGET = 75
DISCOVERY_PAGE_SIZE = 100
CHECKPOINT_INTERVAL = 10
BATCH_RETRY_DELAYS = (2, 5, 10)
JSON_REPLACE_RETRY_DELAYS = (0.5, 1, 2, 4)

DATA_DIRECTORY = Path(__file__).parent / "data"
CATALOG_PATH = DATA_DIRECTORY / "anime_catalog.json"
FAILURES_PATH = DATA_DIRECTORY / "catalog_failures.json"

REQUIRED_CATALOG_FIELDS = {
    "mal_id",
    "title",
    "image_url",
    "score",
    "popularity_rank",
    "members",
    "entry_episodes",
    "series_episodes",
    "release_date",
    "type",
    "runtime_minutes",
}


def load_json_list(path):
    if not path.exists():
        return []

    try:
        with path.open(encoding="utf-8") as json_file:
            data = json.load(json_file)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{path} does not contain valid JSON.") from error

    if not isinstance(data, list):
        raise RuntimeError(f"{path} must contain a JSON list.")

    return data


def write_json(path, data, retry_delays=JSON_REPLACE_RETRY_DELAYS):
    path = Path(path)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")

    try:
        json_text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    except (TypeError, ValueError) as error:
        print(f"Warning: could not serialize JSON for {path}: {error}")
        return False

    try:
        temporary_path.unlink(missing_ok=True)
    except OSError:
        # The retry loop below may still be able to overwrite or replace it.
        pass

    temporary_file_is_ready = False
    last_error = None

    for attempt_number in range(len(retry_delays) + 1):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)

            if not temporary_file_is_ready:
                temporary_path.write_text(json_text, encoding="utf-8")
                temporary_file_is_ready = True

            os.replace(temporary_path, path)
            return True
        except OSError as error:
            last_error = error

            try:
                temporary_file_exists = temporary_path.exists()
            except OSError:
                temporary_file_exists = False

            if not temporary_file_exists:
                temporary_file_is_ready = False

            if attempt_number < len(retry_delays):
                time.sleep(retry_delays[attempt_number])

    try:
        temporary_path.unlink(missing_ok=True)
    except OSError:
        pass

    print(
        f"Warning: could not write {path} after "
        f"{len(retry_delays) + 1} attempts: {last_error}"
    )
    return False


def add_failure(failures, mal_id, title, stage, reason):
    failures.append(
        {
            "mal_id": mal_id,
            "title": title,
            "stage": stage,
            "reason": reason,
        }
    )


def discover_ranking_candidates(ranking_type, target, page_size, failures):
    candidates = []
    discovered_ids = set()
    offset = 0

    while len(candidates) < target:
        request_limit = min(page_size, target - len(candidates))

        try:
            page = fetch_anime_ranking(
                ranking_type,
                limit=request_limit,
                offset=offset,
            )
        except Exception as error:
            add_failure(
                failures,
                None,
                None,
                f"candidate_discovery:{ranking_type}",
                f"{type(error).__name__}: {error}",
            )
            break

        if page is None:
            add_failure(
                failures,
                None,
                None,
                f"candidate_discovery:{ranking_type}",
                "MAL ranking request failed after retries.",
            )
            break

        if not page:
            break

        for candidate in page:
            mal_id = candidate.get("mal_id")

            if mal_id is not None and mal_id not in discovered_ids:
                discovered_ids.add(mal_id)
                candidates.append(candidate)

                if len(candidates) == target:
                    break

        offset += len(page)

        if len(page) < request_limit:
            break

    return candidates


def discover_candidates(
    popular_target=POPULAR_CANDIDATE_TARGET,
    movie_target=MOVIE_CANDIDATE_TARGET,
    page_size=DISCOVERY_PAGE_SIZE,
):
    failures = []
    popular_candidates = discover_ranking_candidates(
        "bypopularity",
        popular_target,
        page_size,
        failures,
    )
    movie_candidates = discover_ranking_candidates(
        "movie",
        movie_target,
        page_size,
        failures,
    )

    candidates_by_id = {}

    for candidate in popular_candidates + movie_candidates:
        mal_id = candidate["mal_id"]

        if mal_id not in candidates_by_id:
            candidates_by_id[mal_id] = candidate

    return list(candidates_by_id.values()), failures


def is_complete_catalog_record(record):
    return (
        isinstance(record, dict)
        and REQUIRED_CATALOG_FIELDS.issubset(record)
        and record["mal_id"] is not None
        and record["series_episodes"] is not None
    )


def sorted_catalog(catalog_by_id):
    return sorted(catalog_by_id.values(), key=lambda anime: anime["mal_id"])


def export_catalog_snapshot(database_path=DATABASE_PATH, catalog_path=CATALOG_PATH):
    catalog = [
        anime
        for anime in load_anime_records(database_path)
        if anime.get("series_episodes") is not None
    ]
    snapshot_exported = write_json(Path(catalog_path), catalog)
    return catalog, snapshot_exported


def import_existing_snapshot(catalog_path=CATALOG_PATH, database_path=DATABASE_PATH):
    try:
        existing_catalog = load_json_list(Path(catalog_path))
    except (OSError, RuntimeError) as error:
        print(
            f"Warning: could not read the optional catalog snapshot at "
            f"{catalog_path}: {error}. Continuing from SQLite."
        )
        return 0

    reusable_records = [
        record
        for record in existing_catalog
        if isinstance(record, dict)
        and record.get("mal_id") is not None
        and record.get("title") is not None
    ]

    if reusable_records:
        upsert_anime_records(reusable_records, database_path)

    return len(reusable_records)


def initialize_failure_state(failures_path, database_path):
    try:
        fallback_failures = load_json_list(Path(failures_path))
    except (OSError, RuntimeError) as error:
        print(
            f"Warning: could not read the optional failure report at "
            f"{failures_path}: {error}. Continuing from SQLite."
        )
        fallback_failures = []

    initialize_ingestion_failure_state(fallback_failures, database_path)


def export_build_outputs(
    database_path=DATABASE_PATH,
    catalog_path=CATALOG_PATH,
    failures_path=FAILURES_PATH,
    failures=None,
):
    catalog, snapshot_exported = export_catalog_snapshot(
        database_path,
        catalog_path,
    )
    failures_exported = True

    if failures is not None:
        failures_exported = write_json(Path(failures_path), failures)

    return catalog, snapshot_exported, failures_exported


def build_failure(mal_id, title, stage, reason):
    return {
        "mal_id": mal_id,
        "title": title,
        "stage": stage,
        "reason": reason,
    }


def deduplicate_failures(failures):
    unique_failures = []
    seen_failures = set()

    for failure in failures:
        key = (
            failure.get("mal_id"),
            failure.get("stage"),
            failure.get("reason"),
        )

        if key not in seen_failures:
            seen_failures.add(key)
            unique_failures.append(failure)

    return unique_failures


def fetch_and_store_anime(anime_id, title, database_path):
    anime = load_anime_ingestion_record(anime_id, database_path)

    if anime is not None:
        return anime, None

    print(f"Fetching MAL anime {anime_id}: {title or 'Unknown title'}")

    try:
        fetched_anime = fetch_anime(anime_id)
    except Exception as error:
        return None, build_failure(
            anime_id,
            title,
            "anime_details",
            f"{type(error).__name__}: {error}",
        )

    if fetched_anime is None:
        return None, build_failure(
            anime_id,
            title,
            "anime_details",
            "Anime details could not be fetched after retries.",
        )

    upsert_anime_records([fetched_anime], database_path)
    return load_anime_ingestion_record(anime_id, database_path), None


def fetch_and_store_relations(anime, database_path):
    anime_id = anime["mal_id"]
    relations = load_anime_relations(anime_id, database_path)

    if relations is not None:
        return relations, None

    print(f"Fetching MAL relationships for {anime_id}: {anime['title']}")

    try:
        fetched_relations = fetch_related_anime(anime_id)
    except Exception as error:
        return None, build_failure(
            anime_id,
            anime["title"],
            "anime_relations",
            f"{type(error).__name__}: {error}",
        )

    if fetched_relations is None:
        return None, build_failure(
            anime_id,
            anime["title"],
            "anime_relations",
            "Anime relationships could not be fetched after retries.",
        )

    store_anime_relations(anime_id, fetched_relations, database_path)
    return fetched_relations, None


def resolve_series_graph(anime_id, title, database_path):
    anime_ids_to_visit = [anime_id]
    visited_anime_ids = set()
    known_titles = {anime_id: title}

    while anime_ids_to_visit:
        current_anime_id = anime_ids_to_visit.pop()

        if current_anime_id in visited_anime_ids:
            continue

        anime, failure = fetch_and_store_anime(
            current_anime_id,
            known_titles.get(current_anime_id),
            database_path,
        )

        if failure is not None:
            return None, failure

        relations, failure = fetch_and_store_relations(anime, database_path)

        if failure is not None:
            return None, failure

        visited_anime_ids.add(current_anime_id)

        for relation in relations:
            related_anime_id = relation["mal_id"]

            if relation["relation_type"] in {"prequel", "sequel"}:
                if relation.get("title"):
                    known_titles[related_anime_id] = relation["title"]

        for related_anime_id in load_mainline_neighbor_ids(
            current_anime_id,
            database_path,
        ):
            if related_anime_id not in visited_anime_ids:
                anime_ids_to_visit.append(related_anime_id)

    series_episodes = resolve_and_store_series_episode_count(
        anime_id,
        database_path,
    )

    if series_episodes is None:
        return None, build_failure(
            anime_id,
            title,
            "series_episodes",
            "The persisted mainline graph is incomplete; no partial total was saved.",
        )

    return series_episodes, None


def attempt_catalog_record(pending_record, database_path=DATABASE_PATH):
    candidate = pending_record["candidate"]
    anime_id = candidate["mal_id"]
    title = candidate.get("title")
    anime = load_anime_ingestion_record(anime_id, database_path)
    retry_stage = candidate.get("retry_stage")

    anime_is_resolved = anime is not None and anime["series_episodes"] is not None
    relations_are_resolved = (
        retry_stage != "anime_relations"
        or load_anime_relations(anime_id, database_path) is not None
    )

    if anime_is_resolved and relations_are_resolved:
        return anime

    _, failure = resolve_series_graph(anime_id, title, database_path)

    if failure is not None:
        pending_record["failure"] = failure
        return None

    return load_anime_ingestion_record(anime_id, database_path)


def build_catalog(
    candidates,
    failures=None,
    retry_delays=BATCH_RETRY_DELAYS,
    database_path=DATABASE_PATH,
    catalog_path=CATALOG_PATH,
    failures_path=FAILURES_PATH,
    import_snapshot=True,
    export_snapshot=True,
):
    if failures is None:
        failures = []

    database_path = Path(database_path)
    catalog_path = Path(catalog_path)
    failures_path = Path(failures_path)
    initialize_database(database_path)

    if import_snapshot:
        import_existing_snapshot(catalog_path, database_path)

    initialize_failure_state(failures_path, database_path)

    pending_by_id = {}

    for candidate in candidates:
        anime_id = candidate["mal_id"]
        stored_anime = load_anime_ingestion_record(anime_id, database_path)
        retry_stage = candidate.get("retry_stage")
        relations_are_resolved = (
            retry_stage != "anime_relations"
            or load_anime_relations(anime_id, database_path) is not None
        )

        if (
            (
                stored_anime is None
                or stored_anime["series_episodes"] is None
                or not relations_are_resolved
            )
            and anime_id not in pending_by_id
        ):
            pending_by_id[anime_id] = {
                "candidate": candidate,
                "failure": candidate.get("retry_failure"),
            }
        else:
            remove_ingestion_failure(anime_id, database_path)

    pass_delays = (0, *retry_delays)

    for pass_number, retry_delay in enumerate(pass_delays, start=1):
        if not pending_by_id:
            break

        if retry_delay:
            print(
                f"Waiting {retry_delay} seconds before catalog retry pass "
                f"{pass_number - 1}."
            )
            time.sleep(retry_delay)

        for anime_id in list(pending_by_id):
            pending_record = pending_by_id[anime_id]
            previous_failure = pending_record.get("failure")
            catalog_record = attempt_catalog_record(pending_record, database_path)

            if catalog_record is not None:
                if previous_failure is not None:
                    remove_ingestion_failure(
                        previous_failure["mal_id"],
                        database_path,
                    )

                remove_ingestion_failure(anime_id, database_path)
                del pending_by_id[anime_id]
                continue

            latest_failure = pending_record["failure"]
            upsert_ingestion_failure(latest_failure, database_path)

            if (
                previous_failure is not None
                and previous_failure["mal_id"] != latest_failure["mal_id"]
            ):
                remove_ingestion_failure(
                    previous_failure["mal_id"],
                    database_path,
                )

    unresolved_failures = load_ingestion_failures(database_path)
    final_failures = deduplicate_failures([*failures, *unresolved_failures])

    if export_snapshot:
        catalog, snapshot_exported, failures_exported = export_build_outputs(
            database_path,
            catalog_path,
            failures_path,
            final_failures,
        )
    else:
        catalog = []
        snapshot_exported = True
        failures_exported = write_json(failures_path, final_failures)

    if not snapshot_exported:
        print("\nDatabase ingestion completed successfully.")

        if failures_exported:
            print(
                "Only the readable JSON catalog snapshot export failed. "
                "The complete snapshot can be regenerated from SQLite on a later run."
            )
        else:
            print(
                "Secondary catalog and failure-report JSON exports failed. "
                "All successfully ingested data remains stored in SQLite."
            )

    return catalog, final_failures


def retry_failed_catalog_records(
    database_path=DATABASE_PATH,
    catalog_path=CATALOG_PATH,
    failures_path=FAILURES_PATH,
    retry_delays=BATCH_RETRY_DELAYS,
):
    database_path = Path(database_path)
    catalog_path = Path(catalog_path)
    failures_path = Path(failures_path)
    initialize_database(database_path)
    initialize_failure_state(failures_path, database_path)

    loaded_failures = load_ingestion_failures(database_path)
    loaded_failure_ids = {failure["mal_id"] for failure in loaded_failures}
    request_count_before = get_mal_api_request_count()

    if loaded_failures:
        retry_candidates = [
            {
                "mal_id": failure["mal_id"],
                "title": failure.get("title"),
                "retry_stage": failure.get("stage"),
                "retry_failure": failure,
            }
            for failure in loaded_failures
        ]
        build_catalog(
            retry_candidates,
            retry_delays=retry_delays,
            database_path=database_path,
            catalog_path=catalog_path,
            failures_path=failures_path,
            import_snapshot=False,
            export_snapshot=False,
        )
    else:
        write_json(failures_path, [])

    unresolved_failures = load_ingestion_failures(database_path)
    unresolved_ids = {failure["mal_id"] for failure in unresolved_failures}
    recovered_count = len(loaded_failure_ids - unresolved_ids)
    request_count_after = get_mal_api_request_count()

    print("\nCatalog failure retry summary")
    print(f"Failures loaded: {len(loaded_failures)}")
    print(f"Recovered: {recovered_count}")
    print(f"Still unresolved: {len(unresolved_failures)}")
    print(
        "MAL request attempts: "
        f"{request_count_after - request_count_before}"
    )

    return {
        "failures_loaded": len(loaded_failures),
        "recovered": recovered_count,
        "still_unresolved": len(unresolved_failures),
        "mal_request_attempts": request_count_after - request_count_before,
    }


def print_summary(
    candidate_count,
    failures,
    database_path,
    newly_added,
    existing_reused,
    api_requests,
):
    summary = load_ingestion_summary(database_path)
    media_type_counts = summary["media_type_counts"]

    print("\nCatalog build summary")
    print(f"Candidates discovered this run: {candidate_count}")
    print(f"Total anime stored in SQLite: {summary['total_anime']}")
    print(f"Newly added anime: {newly_added}")
    print(f"Existing anime reused: {existing_reused}")
    print(f"Unresolved failures: {len(failures)}")
    print(f"Movie count: {media_type_counts.get('movie', 0)}")
    print(f"Relationship rows stored: {summary['relationship_rows']}")
    print(f"Anime with resolved series_episodes: {summary['resolved_series']}")
    print(f"Anime missing series_episodes: {summary['missing_series']}")
    print(f"MAL API request attempts this run: approximately {api_requests}")
    print("Media type counts:")

    for media_type, count in sorted(media_type_counts.items()):
        print(f"  {media_type}: {count}")


def positive_integer(value):
    integer_value = int(value)

    if integer_value <= 0:
        raise argparse.ArgumentTypeError("limits must be positive integers")

    return integer_value


def parse_arguments(arguments=None):
    parser = argparse.ArgumentParser(
        description="Build or resume the local MAL anime catalog."
    )
    parser.add_argument(
        "--anime-limit",
        type=positive_integer,
        default=POPULAR_CANDIDATE_TARGET,
        help="number of popularity-ranking candidates for a normal build",
    )
    parser.add_argument(
        "--movie-limit",
        type=positive_integer,
        default=MOVIE_CANDIDATE_TARGET,
        help="number of movie-ranking candidates for a normal build",
    )
    parser.add_argument(
        "--retry-failures",
        action="store_true",
        help=(
            "retry only unresolved catalog failures without running "
            "ranking discovery"
        ),
    )
    return parser.parse_args(arguments)


def main(arguments=None):
    args = parse_arguments(arguments)

    if args.retry_failures:
        retry_failed_catalog_records()
        return

    request_count_before = get_mal_api_request_count()
    candidates, discovery_failures = discover_candidates(
        popular_target=args.anime_limit,
        movie_target=args.movie_limit,
    )

    if not candidates:
        write_json(FAILURES_PATH, discovery_failures)
        raise RuntimeError(
            "No MAL ranking candidates were discovered. See catalog_failures.json."
        )

    initialize_database(DATABASE_PATH)
    import_existing_snapshot(CATALOG_PATH, DATABASE_PATH)
    anime_ids_before = {
        anime["mal_id"] for anime in load_anime_records(DATABASE_PATH)
    }
    candidate_ids = {candidate["mal_id"] for candidate in candidates}
    existing_reused = len(candidate_ids & anime_ids_before)

    _, failures = build_catalog(candidates, discovery_failures)
    anime_ids_after = {
        anime["mal_id"] for anime in load_anime_records(DATABASE_PATH)
    }
    request_count_after = get_mal_api_request_count()
    print_summary(
        len(candidates),
        failures,
        DATABASE_PATH,
        newly_added=len(anime_ids_after - anime_ids_before),
        existing_reused=existing_reused,
        api_requests=request_count_after - request_count_before,
    )


if __name__ == "__main__":
    main()
