import json
from pathlib import Path

from mal_client import fetch_anime, fetch_series_episode_count


# Local seed list for the first catalog build.
ANIME_IDS = [
    # Popular, multi-season, older, and newer series
    5114,   # Fullmetal Alchemist: Brotherhood
    16498,  # Attack on Titan
    1535,   # Death Note
    9253,   # Steins;Gate
    11061,  # Hunter x Hunter (2011)
    21,     # One Piece
    20,     # Naruto
    1735,   # Naruto: Shippuden
    269,    # Bleach
    38000,  # Demon Slayer
    40748,  # Jujutsu Kaisen
    31964,  # My Hero Academia
    30276,  # One Punch Man
    11757,  # Sword Art Online
    19815,  # No Game No Life
    20583,  # Haikyu!!
    32182,  # Mob Psycho 100
    38691,  # Dr. Stone
    37521,  # Vinland Saga
    52991,  # Frieren: Beyond Journey's End
    44511,  # Chainsaw Man
    235,    # Detective Conan
    1575,   # Code Geass
    30,     # Neon Genesis Evangelion
    19,     # Monster
    # Movies with varied runtimes
    199,    # Spirited Away
    32281,  # Your Name
    164,    # Princess Mononoke
    47,     # Akira
    28851,  # A Silent Voice
    437,    # Perfect Blue
    43,     # Ghost in the Shell
    6675,   # Redline
    2236,   # The Girl Who Leapt Through Time
    38826,  # Weathering with You
    50594,  # Suzume
    12355,  # Wolf Children
]

CATALOG_PATH = Path(__file__).parent / "data" / "anime_catalog.json"


def build_catalog():
    catalog = []

    for anime_id in ANIME_IDS:
        print(f"Fetching MAL anime {anime_id}...")
        anime = fetch_anime(anime_id)

        if anime is None:
            print(f"Skipping MAL anime {anime_id} because it could not be fetched.")
            continue

        series_episodes = fetch_series_episode_count(anime_id)

        if series_episodes is None:
            print(
                f"Skipping MAL anime {anime_id} because its series episode total "
                "could not be determined reliably."
            )
            continue

        catalog_record = anime.copy()
        catalog_record["series_episodes"] = series_episodes
        catalog.append(catalog_record)

    return catalog


def save_catalog(catalog):
    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    with CATALOG_PATH.open("w", encoding="utf-8") as catalog_file:
        json.dump(catalog, catalog_file, ensure_ascii=False, indent=2)
        catalog_file.write("\n")


def main():
    catalog = build_catalog()
    save_catalog(catalog)
    print(f"Saved {len(catalog)} anime to {CATALOG_PATH}")


if __name__ == "__main__":
    main()
