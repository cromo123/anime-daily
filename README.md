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
- **More Popular:** Choose which anime has more members or users.
- **More Episodes:** Choose which anime has the larger episode count.
- **More Recent:** Choose which anime aired or was released more recently.
- **Longer Runtime:** For anime movies only, choose which movie has the longer runtime.

Each category will use a different ordered list of six anime. Players will make five chained comparisons: anime 1 vs anime 2, anime 2 vs anime 3, and so on through anime 5 vs anime 6. After every choice, the relevant metric for both anime will be revealed whether the answer was correct or not.

After five comparisons, the game will move to the next category with a different anime list. Five categories with five comparisons each will produce a total daily score out of 25. Longer Runtime will compare complete anime movie runtimes only, not normal TV episode runtimes.

## Planned Architecture / Technology

- Python for data processing and deterministic game logic
- FastAPI for the application API
- External anime APIs as structured data sources
- A database for anime data, challenges, and results
- Automated testing for game logic, data validation, and API behavior
- CrewAI for agent-based curation and challenge review
- Automated daily challenge generation
- Deployment infrastructure for reliable scheduled operation

## AI and Factual Integrity

AI may make subjective curation decisions, such as selecting compelling comparisons or flagging low-quality matchups. It must never determine factual game answers. All answers will be calculated and validated through deterministic Python logic using structured source data.
