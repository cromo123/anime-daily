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
        "episodes": data.get("num_episodes"),
        "release_date": data.get("start_date"),
        "type": data.get("media_type"),
        "runtime_minutes": runtime_minutes,
    }

    return anime


anime = fetch_anime(32281)

if anime is not None:
    print(anime)