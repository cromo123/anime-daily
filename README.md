# Anime Daily

Anime Daily is a daily anime comparison web game inspired by higher/lower games. Players compare anime using factual metrics such as score, popularity, episode count, and release date.

The long-term goal is to deliver one shared challenge each day, generated automatically from structured anime data. AI agents will help curate interesting matchups and review challenge quality, while deterministic Python logic will validate every factual answer.

## Current Status

Anime Daily is in early development. Its gameplay, data pipeline, and supporting infrastructure are currently being designed and built.

## Planned Features

- One shared anime comparison challenge each day
- Five comparison categories with a total daily score out of 25
- Automatically generated matchups from structured anime data
- AI-assisted matchup curation and quality review
- Deterministic validation of factual game answers
- A responsive web experience with clear daily results

## Planned Daily Challenge

Every category except More Episodes will compare the specific MAL entry or season shown:

- **Higher Score:** Compare the user score of each specific entry.
- **More Popular:** Compare the member or list-user count of each specific entry, then reveal both that count and its MAL popularity rank.
- **More Episodes:** Compare the calculated episode total of each connected mainline series. This is the only category that uses whole-series aggregation.
- **More Recent:** Compare the start or release date of each specific entry.
- **Longer Runtime:** Compare the runtime of each specific movie entry. Normal TV episode runtimes are not used.

Each category will use a different ordered list of six anime. Players will make five chained comparisons: anime 1 vs anime 2, anime 2 vs anime 3, and so on through anime 5 vs anime 6. After every choice, the relevant metric for both anime will be revealed whether the answer was correct or not.

After five comparisons, the game will move to the next category with a different anime list. Five categories with five comparisons each will produce a total daily score out of 25.

## Data and Catalog Strategy

The official MyAnimeList API will be the authoritative external source for anime facts. The finished application will synchronize that data into its own local catalog and database, so gameplay and daily challenge generation will not depend on live MAL requests.

The catalog is intended to cover tens of thousands of popular anime, with especially strong movie coverage for Longer Runtime. MAL data will be refreshed periodically rather than treated as permanently static.

## Series Episode Totals

MAL represents seasons and other releases as separate entries. The catalog will preserve each entry's episode count as `entry_episodes` and calculate `series_episodes` specifically for More Episodes. The series total represents the number of episodic installments across the connected mainline series; it will not replace or affect score, popularity, release date, or any other entry-specific metric.

Series totals will be derived deterministically by following only MAL prequel and sequel relationships. Mainline TV, ONA, OVA, Special, and TV Special entries may contribute episodes. Movies may be followed when they connect mainline entries, but they will not contribute to the total. Side stories, spin-offs, character relations, alternate versions, and unrelated entries will not be included in this traversal.

## Challenge History

Previous daily challenges and matchup history will be stored so generation can avoid recently reused anime when practical and strongly avoid recent exact matchup repeats. Anime and pairings may return eventually, but repetition will be controlled rather than random.

## Planned Architecture / Technology

- Python for data processing and deterministic game logic
- FastAPI for the application API
- The official MyAnimeList API as the authoritative external anime data source
- A synchronized local database for the anime catalog, challenges, and game history
- Automated testing for game logic, data validation, and API behavior
- CrewAI for agent-based curation and challenge review
- Automated daily challenge generation
- Deployment infrastructure for reliable scheduled operation

## AI and Factual Integrity

AI agents may eventually curate interesting candidate sets, select compelling matchups, or flag low-quality options. They must never determine factual answers or be trusted to guarantee matchup validity. Before a matchup can be accepted, deterministic Python validation must confirm that both comparison values exist and are different, preventing ties and missing-data matchups.
