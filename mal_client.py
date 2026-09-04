import os
import time

import httpx
from dotenv import load_dotenv


load_dotenv()
mal_client_id = os.getenv("MAL_CLIENT_ID")

if mal_client_id is None:
    raise RuntimeError("MAL_CLIENT_ID is missing from .env")


MAX_REQUEST_ATTEMPTS = 3
_anime_cache = {}
_related_anime_cache = {}
_mal_api_request_count = 0


def _request_mal(url, params):
    global _mal_api_request_count

    headers = {
        "X-MAL-CLIENT-ID": mal_client_id,
    }

    for attempt_number in range(1, MAX_REQUEST_ATTEMPTS + 1):
        try:
            _mal_api_request_count += 1
            response = httpx.get(
                url,
                headers=headers,
                params=params,
                timeout=10.0,
                follow_redirects=True,
            )

            response.raise_for_status()
            return response

        except httpx.HTTPStatusError as error:
            status_code = error.response.status_code
            is_server_error = 500 <= status_code < 600

            if is_server_error and attempt_number < MAX_REQUEST_ATTEMPTS:
                time.sleep(attempt_number)
                continue

            print(f"MAL API returned an error: {status_code}")
            return None

        except httpx.RequestError as error:
            if attempt_number < MAX_REQUEST_ATTEMPTS:
                time.sleep(attempt_number)
                continue

            print(f"Request failed: {error}")
            return None

    return None


def get_mal_api_request_count():
    """Return the number of MAL HTTP attempts made by this Python process."""
    return _mal_api_request_count


def fetch_anime_ranking(ranking_type, limit=100, offset=0):
    url = "https://api.myanimelist.net/v2/anime/ranking"

    params = {
        "ranking_type": ranking_type,
        "limit": limit,
        "offset": offset,
        "fields": "media_type",
    }

    response = _request_mal(url, params)

    if response is None:
        return None

    data = response.json()
    ranked_anime = []

    for ranking_entry in data.get("data", []):
        anime = ranking_entry["node"]
        ranked_anime.append(
            {
                "mal_id": anime["id"],
                "title": anime["title"],
                "type": anime.get("media_type"),
            }
        )

    return ranked_anime


def fetch_anime(anime_id):
    if anime_id in _anime_cache:
        return _anime_cache[anime_id]

    url = f"https://api.myanimelist.net/v2/anime/{anime_id}"

    params = {
        "fields": (
            "id,title,main_picture,mean,popularity,num_list_users,"
            "num_episodes,start_date,media_type,average_episode_duration"
        )
    }

    response = _request_mal(url, params)

    if response is None:
        return None

    data = response.json()

    runtime_seconds = data.get("average_episode_duration")

    if runtime_seconds is not None:
        runtime_minutes = runtime_seconds // 60
    else:
        runtime_minutes = None

    main_picture = data.get("main_picture")

    if isinstance(main_picture, dict):
        image_url = main_picture.get("large") or main_picture.get("medium")
    else:
        image_url = None

    anime = {
        "mal_id": data["id"],
        "title": data["title"],
        "image_url": image_url,
        "score": data.get("mean"),
        "popularity_rank": data.get("popularity"),
        "members": data.get("num_list_users"),
        "entry_episodes": data.get("num_episodes"),
        "release_date": data.get("start_date"),
        "type": data.get("media_type"),
        "runtime_minutes": runtime_minutes,
    }

    _anime_cache[anime_id] = anime

    return anime


def fetch_related_anime(anime_id):
    if anime_id in _related_anime_cache:
        return _related_anime_cache[anime_id]

    url = f"https://api.myanimelist.net/v2/anime/{anime_id}"

    params = {
        "fields": "related_anime",
    }

    response = _request_mal(url, params)

    if response is None:
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

    _related_anime_cache[anime_id] = related_anime

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

        if anime is None:
            return None

        if anime["type"] in ["tv", "ona", "ova", "special", "tv_special"]:
            entry_episodes = anime["entry_episodes"]

            if entry_episodes is not None:
                episode_total += entry_episodes

        related_anime = fetch_related_anime(current_anime_id)

        if related_anime is None:
            return None

        for relation in related_anime:
            if relation["relation_type"] in ["prequel", "sequel"]:
                related_anime_id = relation["mal_id"]

                if related_anime_id not in visited_anime_ids:
                    anime_ids_to_visit.append(related_anime_id)

    return episode_total
