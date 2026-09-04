import time
from pathlib import Path

from build_catalog import (
    BATCH_RETRY_DELAYS,
    CATALOG_PATH,
    CHECKPOINT_INTERVAL,
    load_json_list,
    write_json,
)
from database import DATABASE_PATH, initialize_database, upsert_anime_records
from mal_client import fetch_anime


FAILURES_PATH = Path(__file__).parent / "data" / "image_refresh_failures.json"


def save_refresh_progress(
    catalog,
    refreshed_records,
    catalog_path,
    database_path,
    failures_path,
    failures=None,
):
    if refreshed_records:
        upsert_anime_records(refreshed_records, database_path)

    write_json(catalog_path, catalog)

    if failures is not None:
        write_json(failures_path, failures)

    refreshed_records.clear()


def attempt_image_refresh(pending_record):
    anime = pending_record["anime"]
    mal_id = anime.get("mal_id")
    title = anime.get("title")
    print(f"Fetching image for MAL anime {mal_id}: {title or 'Unknown title'}")

    try:
        refreshed_anime = fetch_anime(mal_id)
    except Exception as error:
        pending_record["failure"] = {
            "mal_id": mal_id,
            "title": title,
            "reason": f"{type(error).__name__}: {error}",
        }
        return False

    if refreshed_anime is None:
        pending_record["failure"] = {
            "mal_id": mal_id,
            "title": title,
            "reason": "Anime details could not be fetched after retries.",
        }
        return False

    anime["image_url"] = refreshed_anime.get("image_url")
    return True


def refresh_image_urls(
    catalog_path=CATALOG_PATH,
    database_path=DATABASE_PATH,
    failures_path=FAILURES_PATH,
    retry_delays=BATCH_RETRY_DELAYS,
):
    catalog = load_json_list(Path(catalog_path))
    initialize_database(database_path)

    refreshed_records = []
    already_checked = 0
    successful_fetches = 0
    attempted_fetches = 0
    pending_by_id = {}

    for anime in catalog:
        if "image_url" in anime:
            already_checked += 1
            continue

        mal_id = anime.get("mal_id")
        pending_by_id[mal_id] = {
            "anime": anime,
            "failure": None,
        }

    pass_delays = (0, *retry_delays)

    for pass_number, retry_delay in enumerate(pass_delays, start=1):
        if not pending_by_id:
            break

        if retry_delay:
            print(
                f"Waiting {retry_delay} seconds before image retry pass "
                f"{pass_number - 1}."
            )
            time.sleep(retry_delay)

        for mal_id in list(pending_by_id):
            pending_record = pending_by_id[mal_id]
            attempted_fetches += 1

            if attempt_image_refresh(pending_record):
                refreshed_records.append(pending_record["anime"])
                successful_fetches += 1
                del pending_by_id[mal_id]

            if attempted_fetches % CHECKPOINT_INTERVAL == 0:
                save_refresh_progress(
                    catalog,
                    refreshed_records,
                    catalog_path,
                    database_path,
                    failures_path,
                )

    unresolved_failures = [
        pending_record["failure"] for pending_record in pending_by_id.values()
    ]

    save_refresh_progress(
        catalog,
        refreshed_records,
        catalog_path,
        database_path,
        failures_path,
        unresolved_failures,
    )

    checked_records = [anime for anime in catalog if "image_url" in anime]

    if checked_records:
        upsert_anime_records(checked_records, database_path)

    records_with_image_urls = [
        anime for anime in catalog if anime.get("image_url") is not None
    ]

    print("\nImage refresh summary")
    print(f"Catalog records: {len(catalog)}")
    print(f"Previously checked records: {already_checked}")
    print(f"Records fetched successfully: {successful_fetches}")
    print(f"Records with an image URL: {len(records_with_image_urls)}")
    print(f"Failures after all retry passes: {len(unresolved_failures)}")

    return catalog, unresolved_failures


if __name__ == "__main__":
    refresh_image_urls()
