# Anime Daily

Anime Daily is a daily anime comparison web game inspired by higher/lower games. Players compare anime using factual metrics such as score, popularity, episode count, and release date.

The long-term goal is to deliver one shared challenge each day, generated automatically from structured anime data. AI agents will help curate interesting matchups and review challenge quality, while deterministic Python logic will validate every factual answer.

## Current Status

Anime Daily is in early development. Its gameplay, data pipeline, and supporting infrastructure are currently being designed and built.

## Planned Features

- One shared anime comparison challenge each day
- Comparisons based on score, popularity, episode count, and release date
- Automatically generated matchups from structured anime data
- AI-assisted matchup curation and quality review
- Deterministic validation of factual game answers
- A responsive web experience with clear daily results

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
