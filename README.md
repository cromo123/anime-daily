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

- **Higher Score:** Choose which anime has the higher user score.
- **More Popular:** Choose which anime has more members or list users, then reveal both MAL popularity rank and member count.
- **More Episodes:** Choose which anime has the larger total episode count across its mainline series, rather than comparing only one MAL entry.
- **More Recent:** Choose which anime aired or was released more recently.
- **Longer Runtime:** For anime movies only, choose which movie has the longer runtime.

Each category will use a different ordered list of six anime. Players will make five chained comparisons: anime 1 vs anime 2, anime 2 vs anime 3, and so on through anime 5 vs anime 6. After every choice, the relevant metric for both anime will be revealed whether the answer was correct or not.

After five comparisons, the game will move to the next category with a different anime list. Five categories with five comparisons each will produce a total daily score out of 25. Longer Runtime will compare complete anime movie runtimes only, not normal TV episode runtimes.

Every matchup will require two present and unequal values for its comparison metric. AI agents may later help curate interesting candidates, but deterministic Python validation will guarantee factual answers and prevent ties.

## Data and Catalog Strategy

The official MyAnimeList API will be the authoritative external source for anime facts. The finished application will synchronize that data into its own local catalog and database, so gameplay and daily challenge generation will not depend on live MAL requests.

The catalog is intended to cover tens of thousands of popular anime, with especially strong movie coverage for Longer Runtime. Stored game history will help daily generation limit recently reused anime and prioritize avoiding recent exact matchups. Anime and matchups may return over time, but repetition will be controlled rather than accidental.

## Series Episode Totals

MAL entry-level episode counts will be stored separately from calculated series-level totals. Mainline totals will be derived deterministically from MAL prequel and sequel relationships.

Mainline TV, ONA, OVA, Special, and TV Special entries may contribute episodes. Movies may still be followed when they connect mainline entries, but they will not contribute to the series episode total.

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

AI may make subjective curation decisions, such as selecting compelling comparisons or flagging low-quality matchups. It must never determine factual game answers. All answers will be calculated and validated through deterministic Python logic using structured source data.
