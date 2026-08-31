import os

import httpx
from dotenv import load_dotenv


load_dotenv()
mal_client_id = os.getenv("MAL_CLIENT_ID")

if mal_client_id is None:
    raise RuntimeError("MAL_CLIENT_ID is missing from .env")


def fetch_anime(anime_id):
    url = f"https://api.myanimelist.net/v2/anime/{anime_id}"

    headers = {
        "X-MAL-CLIENT-ID": mal_client_id,
    }

    params = {
        "fields": (
            "id,title,mean,popularity,num_list_users,"
            "num_episodes,start_date,media_type,average_episode_duration"
        )
    }

    try:
        response = httpx.get(
            url,
            headers=headers,
            params=params,
            timeout=10.0,
        )

        response.raise_for_status()

    except httpx.HTTPStatusError as error:
        print(f"MAL API returned an error: {error.response.status_code}")
        return None

    except httpx.RequestError as error:
        print(f"Request failed: {error}")
        return None

    data = response.json()

    runtime_seconds = data.get("average_episode_duration")

    if runtime_seconds is not None:
        runtime_minutes = runtime_seconds // 60
    else:
        runtime_minutes = None

    anime = {
        "mal_id": data["id"],
        "title": data["title"],
        "score": data.get("mean"),
        "popularity_rank": data.get("popularity"),
        "members": data.get("num_list_users"),
        "entry_episodes": data.get("num_episodes"),
        "release_date": data.get("start_date"),
        "type": data.get("media_type"),
        "runtime_minutes": runtime_minutes,
    }

    return anime


def fetch_related_anime(anime_id):
    url = f"https://api.myanimelist.net/v2/anime/{anime_id}"

    headers = {
        "X-MAL-CLIENT-ID": mal_client_id,
    }

    params = {
        "fields": "related_anime",
    }

    try:
        response = httpx.get(
            url,
            headers=headers,
            params=params,
            timeout=10.0,
        )

        response.raise_for_status()

    except httpx.HTTPStatusError as error:
        print(f"MAL API returned an error: {error.response.status_code}")
        return None

    except httpx.RequestError as error:
        print(f"Request failed: {error}")
        return None

    data = response.json()
    related_anime = []

    for relation in data.get("related_anime", []):
        related_anime.append(
            {
                "mal_id": relation["node"]["id"],
                "title": relation["node"]["title"],
                "relation_type": relation["relation_type"],
            }
        )

    return related_anime


def fetch_series_episode_count(anime_id):
    episode_total = 0
    anime_ids_to_visit = [anime_id]
    visited_anime_ids = set()

    while anime_ids_to_visit:
        current_anime_id = anime_ids_to_visit.pop()

        if current_anime_id in visited_anime_ids:
            continue

        visited_anime_ids.add(current_anime_id)

        anime = fetch_anime(current_anime_id)

        if anime is not None and anime["type"] in [
            "tv",
            "ona",
            "ova",
            "special",
            "tv_special",
        ]:
            entry_episodes = anime["entry_episodes"]

            if entry_episodes is not None:
                episode_total += entry_episodes

        related_anime = fetch_related_anime(current_anime_id)

        if related_anime is None:
            continue

        for relation in related_anime:
            if relation["relation_type"] in ["prequel", "sequel"]:
                related_anime_id = relation["mal_id"]

                if related_anime_id not in visited_anime_ids:
                    anime_ids_to_visit.append(related_anime_id)

    return episode_total
