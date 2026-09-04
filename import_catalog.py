import json
from pathlib import Path

from database import DATABASE_PATH, load_anime_records, upsert_anime_records


CATALOG_PATH = Path(__file__).parent / "data" / "anime_catalog.json"


def load_catalog_file(catalog_path=CATALOG_PATH):
    try:
        with Path(catalog_path).open(encoding="utf-8") as catalog_file:
            catalog = json.load(catalog_file)
    except FileNotFoundError as error:
        raise RuntimeError(f"Anime catalog not found at {catalog_path}.") from error
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Anime catalog at {catalog_path} is not valid JSON.") from error

    if not isinstance(catalog, list):
        raise RuntimeError("Anime catalog must contain a JSON list of anime entries.")

    return catalog


def import_catalog(catalog_path=CATALOG_PATH, database_path=DATABASE_PATH):
    catalog = load_catalog_file(catalog_path)
    imported_count = upsert_anime_records(catalog, database_path)
    total_count = len(load_anime_records(database_path))

    print(f"Catalog records read: {len(catalog)}")
    print(f"Anime records upserted: {imported_count}")
    print(f"Total anime rows in database: {total_count}")

    return imported_count, total_count


if __name__ == "__main__":
    import_catalog()
