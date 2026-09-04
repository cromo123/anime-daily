import json
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


def save_build_progress(catalog_by_id, failures):
    write_json(CATALOG_PATH, sorted_catalog(catalog_by_id))
    write_json(FAILURES_PATH, failures)


def build_catalog(candidates, failures=None):
    if failures is None:
        failures = []

    existing_catalog = load_json_list(CATALOG_PATH)
    catalog_by_id = {
        record["mal_id"]: record
        for record in existing_catalog
        if is_complete_catalog_record(record)
    }

    for candidate_number, candidate in enumerate(candidates, start=1):
        anime_id = candidate["mal_id"]
        title = candidate.get("title")

        if anime_id not in catalog_by_id:
            print(f"Fetching MAL anime {anime_id}: {title or 'Unknown title'}")

            try:
                anime = fetch_anime(anime_id)
            except Exception as error:
                anime = None
                add_failure(
                    failures,
                    anime_id,
                    title,
                    "anime_details",
                    f"{type(error).__name__}: {error}",
                )

            if anime is None:
                if not any(
                    failure["mal_id"] == anime_id
                    and failure["stage"] == "anime_details"
                    for failure in failures
                ):
                    add_failure(
                        failures,
                        anime_id,
                        title,
                        "anime_details",
                        "Anime details could not be fetched after retries.",
                    )
            else:
                try:
                    series_episodes = fetch_series_episode_count(anime_id)
                except Exception as error:
                    series_episodes = None
                    add_failure(
                        failures,
                        anime_id,
                        anime["title"],
                        "series_episodes",
                        f"{type(error).__name__}: {error}",
                    )

                if series_episodes is None:
                    if not any(
                        failure["mal_id"] == anime_id
                        and failure["stage"] == "series_episodes"
                        for failure in failures
                    ):
                        add_failure(
                            failures,
                            anime_id,
                            anime["title"],
                            "series_episodes",
                            "Series episode total could not be determined reliably.",
                        )
                else:
                    catalog_record = anime.copy()
                    catalog_record["series_episodes"] = series_episodes
                    catalog_by_id[anime_id] = catalog_record

        if candidate_number % CHECKPOINT_INTERVAL == 0:
            save_build_progress(catalog_by_id, failures)

    save_build_progress(catalog_by_id, failures)

    return sorted_catalog(catalog_by_id), failures


def print_summary(candidate_count, catalog, failures):
    media_type_counts = Counter(
        anime.get("type") or "unknown" for anime in catalog
    )

    print("\nCatalog build summary")
    print(f"Candidates discovered: {candidate_count}")
    print(f"Records successfully saved: {len(catalog)}")
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
