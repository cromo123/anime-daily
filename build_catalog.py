import json
import time
from collections import Counter
from pathlib import Path

from mal_client import (
    fetch_anime,
    fetch_anime_ranking,
    fetch_series_episode_count,
)


POPULAR_CANDIDATE_TARGET = 350
MOVIE_CANDIDATE_TARGET = 75
DISCOVERY_PAGE_SIZE = 100
CHECKPOINT_INTERVAL = 10
BATCH_RETRY_DELAYS = (2, 5, 10)

DATA_DIRECTORY = Path(__file__).parent / "data"
CATALOG_PATH = DATA_DIRECTORY / "anime_catalog.json"
FAILURES_PATH = DATA_DIRECTORY / "catalog_failures.json"

REQUIRED_CATALOG_FIELDS = {
    "mal_id",
    "title",
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


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")

    with temporary_path.open("w", encoding="utf-8") as json_file:
        json.dump(data, json_file, ensure_ascii=False, indent=2)
        json_file.write("\n")

    temporary_path.replace(path)


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


def save_build_progress(catalog_by_id, failures=None):
    write_json(CATALOG_PATH, sorted_catalog(catalog_by_id))

    if failures is not None:
        write_json(FAILURES_PATH, failures)


def attempt_catalog_record(pending_record):
    candidate = pending_record["candidate"]
    anime_id = candidate["mal_id"]
    title = candidate.get("title")
    anime = pending_record.get("anime")

    if anime is None:
        print(f"Fetching MAL anime {anime_id}: {title or 'Unknown title'}")

        try:
            anime = fetch_anime(anime_id)
        except Exception as error:
            pending_record["failure"] = {
                "mal_id": anime_id,
                "title": title,
                "stage": "anime_details",
                "reason": f"{type(error).__name__}: {error}",
            }
            return None

        if anime is None:
            pending_record["failure"] = {
                "mal_id": anime_id,
                "title": title,
                "stage": "anime_details",
                "reason": "Anime details could not be fetched after retries.",
            }
            return None

        pending_record["anime"] = anime

    try:
        series_episodes = fetch_series_episode_count(anime_id)
    except Exception as error:
        pending_record["failure"] = {
            "mal_id": anime_id,
            "title": anime["title"],
            "stage": "series_episodes",
            "reason": f"{type(error).__name__}: {error}",
        }
        return None

    if series_episodes is None:
        pending_record["failure"] = {
            "mal_id": anime_id,
            "title": anime["title"],
            "stage": "series_episodes",
            "reason": "Series episode total could not be determined reliably.",
        }
        return None

    catalog_record = anime.copy()
    catalog_record["series_episodes"] = series_episodes
    return catalog_record


def build_catalog(candidates, failures=None, retry_delays=BATCH_RETRY_DELAYS):
    if failures is None:
        failures = []

    existing_catalog = load_json_list(CATALOG_PATH)
    catalog_by_id = {
        record["mal_id"]: record
        for record in existing_catalog
        if is_complete_catalog_record(record)
    }

    pending_by_id = {}

    for candidate in candidates:
        anime_id = candidate["mal_id"]

        if anime_id not in catalog_by_id and anime_id not in pending_by_id:
            pending_by_id[anime_id] = {
                "candidate": candidate,
                "anime": None,
                "failure": None,
            }

    processed_attempts = 0
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
            catalog_record = attempt_catalog_record(pending_record)
            processed_attempts += 1

            if catalog_record is not None:
                catalog_by_id[anime_id] = catalog_record
                del pending_by_id[anime_id]

            if processed_attempts % CHECKPOINT_INTERVAL == 0:
                save_build_progress(catalog_by_id)

    unresolved_failures = [
        pending_record["failure"] for pending_record in pending_by_id.values()
    ]
    final_failures = [*failures, *unresolved_failures]
    save_build_progress(catalog_by_id, final_failures)

    return sorted_catalog(catalog_by_id), final_failures


def print_summary(candidate_count, catalog, failures):
    media_type_counts = Counter(
        anime.get("type") or "unknown" for anime in catalog
    )

    print("\nCatalog build summary")
    print(f"Candidates discovered: {candidate_count}")
    print(f"Total catalog records currently stored: {len(catalog)}")
    print(f"Failures: {len(failures)}")
    print(f"Movie count: {media_type_counts.get('movie', 0)}")
    print("Media type counts:")

    for media_type, count in sorted(media_type_counts.items()):
        print(f"  {media_type}: {count}")


def main():
    candidates, discovery_failures = discover_candidates()

    if not candidates:
        write_json(FAILURES_PATH, discovery_failures)
        raise RuntimeError(
            "No MAL ranking candidates were discovered. See catalog_failures.json."
        )

    catalog, failures = build_catalog(candidates, discovery_failures)
    print_summary(len(candidates), catalog, failures)


if __name__ == "__main__":
    main()
