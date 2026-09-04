from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from challenge import (
    evaluate_comparison,
    get_or_create_daily_challenge,
    load_stored_challenge,
    serialize_public_challenge,
)
from database import DATABASE_PATH


STATIC_DIRECTORY = Path(__file__).parent / "static"

app = FastAPI(title="Anime Daily")
app.state.database_path = DATABASE_PATH
app.mount("/static", StaticFiles(directory=STATIC_DIRECTORY), name="static")


class AnswerRequest(BaseModel):
    category: str
    comparison_position: int
    selected_mal_id: int


def current_challenge_date():
    return date.today().isoformat()


@app.get("/", include_in_schema=False)
def frontend():
    return FileResponse(STATIC_DIRECTORY / "index.html")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/challenge/today")
def get_today_challenge():
    challenge_date = current_challenge_date()

    try:
        challenge = get_or_create_daily_challenge(
            challenge_date,
            app.state.database_path,
        )
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=500, detail=str(error)) from error

    return serialize_public_challenge(challenge_date, challenge)


@app.post("/challenge/today/answer")
def answer_today_comparison(answer: AnswerRequest):
    challenge_date = current_challenge_date()

    try:
        challenge = load_stored_challenge(
            challenge_date,
            app.state.database_path,
        )
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=500, detail=str(error)) from error

    if challenge is None:
        raise HTTPException(
            status_code=404,
            detail="Today's challenge has not been generated yet.",
        )

    try:
        result = evaluate_comparison(
            challenge,
            answer.category,
            answer.comparison_position,
            answer.selected_mal_id,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error

    return {"challenge_date": challenge_date, **result}
