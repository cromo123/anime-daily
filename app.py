import os
import re
import secrets
from calendar import month_name, monthrange
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from challenge import (
    TOTAL_QUESTIONS,
    evaluate_comparison,
    get_or_create_daily_challenge,
    load_stored_challenge,
    serialize_public_challenge,
    verify_completed_answers,
)
from database import (
    DATABASE_PATH,
    ensure_player,
    load_challenge_record,
    load_month_archive,
    load_player_results,
    record_player_result,
)


STATIC_DIRECTORY = Path(__file__).parent / "static"
PLAYER_COOKIE_NAME = "anime_daily_player"
PLAYER_COOKIE_MAX_AGE = 60 * 60 * 24 * 365 * 2
PLAYER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
PLAYER_COOKIE_SECURE = os.getenv("ANIME_DAILY_COOKIE_SECURE", "").lower() in {
    "1",
    "true",
    "yes",
}

app = FastAPI(title="Anime Daily")
app.state.database_path = DATABASE_PATH
app.state.player_cookie_secure = PLAYER_COOKIE_SECURE
app.mount("/static", StaticFiles(directory=STATIC_DIRECTORY), name="static")


class AnswerRequest(BaseModel):
    category: str
    comparison_position: int
    selected_mal_id: int


class CompletionAnswer(BaseModel):
    category: str
    comparison_position: int
    selected_mal_id: int


class CompletionRequest(BaseModel):
    answers: list[CompletionAnswer]


def request_uses_player_identity(path):
    return (
        path == "/"
        or path.startswith("/challenge/")
        or path == "/archive"
        or path == "/player/history"
    )


def valid_player_id(player_id):
    return (
        player_id is not None
        and PLAYER_ID_PATTERN.fullmatch(player_id) is not None
    )


@app.middleware("http")
async def anonymous_player_identity(request: Request, call_next):
    if not request_uses_player_identity(request.url.path):
        return await call_next(request)

    player_id = request.cookies.get(PLAYER_COOKIE_NAME)
    should_set_cookie = not valid_player_id(player_id)

    if should_set_cookie:
        player_id = secrets.token_urlsafe(32)

    ensure_player(player_id, request.app.state.database_path)
    request.state.player_id = player_id
    response = await call_next(request)

    if should_set_cookie:
        response.set_cookie(
            key=PLAYER_COOKIE_NAME,
            value=player_id,
            max_age=PLAYER_COOKIE_MAX_AGE,
            httponly=True,
            secure=request.app.state.player_cookie_secure,
            samesite="lax",
            path="/",
        )

    return response


def current_challenge_date():
    return date.today().isoformat()


def parse_challenge_date(challenge_date):
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", challenge_date) is None:
        raise HTTPException(
            status_code=400,
            detail="challenge date must use YYYY-MM-DD format.",
        )

    try:
        requested_date = date.fromisoformat(challenge_date)
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail="challenge date must use YYYY-MM-DD format.",
        ) from error

    if requested_date > date.today():
        raise HTTPException(
            status_code=400,
            detail="Future challenges are not available.",
        )

    return requested_date


def load_challenge_for_api(requested_date):
    challenge_date = requested_date.isoformat()

    try:
        if requested_date == date.today():
            challenge = get_or_create_daily_challenge(
                challenge_date,
                app.state.database_path,
            )
        else:
            challenge = load_stored_challenge(
                challenge_date,
                app.state.database_path,
            )
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=500, detail=str(error)) from error

    if challenge is None:
        raise HTTPException(status_code=404, detail="Challenge not found.")

    return challenge


def evaluate_answer_for_date(requested_date, answer):
    challenge_date = requested_date.isoformat()

    try:
        challenge = load_stored_challenge(
            challenge_date,
            app.state.database_path,
        )
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=500, detail=str(error)) from error

    if challenge is None:
        raise HTTPException(status_code=404, detail="Challenge not found.")

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


@app.get("/", include_in_schema=False)
def frontend():
    return FileResponse(STATIC_DIRECTORY / "index.html")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/challenge/today")
def get_today_challenge():
    requested_date = date.today()
    challenge = load_challenge_for_api(requested_date)
    return serialize_public_challenge(requested_date, challenge)


@app.get("/challenge/{challenge_date}")
def get_dated_challenge(challenge_date: str):
    requested_date = parse_challenge_date(challenge_date)
    challenge = load_challenge_for_api(requested_date)
    return serialize_public_challenge(requested_date, challenge)


@app.post("/challenge/today/answer")
def answer_today_comparison(answer: AnswerRequest):
    return evaluate_answer_for_date(date.today(), answer)


@app.post("/challenge/{challenge_date}/answer")
def answer_dated_comparison(challenge_date: str, answer: AnswerRequest):
    requested_date = parse_challenge_date(challenge_date)
    return evaluate_answer_for_date(requested_date, answer)


@app.post("/challenge/{challenge_date}/complete")
def complete_challenge(
    challenge_date: str,
    completion: CompletionRequest,
    request: Request,
):
    normalized_date = parse_challenge_date(challenge_date).isoformat()

    try:
        challenge = load_stored_challenge(
            normalized_date,
            app.state.database_path,
        )
        challenge_record = load_challenge_record(
            normalized_date,
            app.state.database_path,
        )
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=500, detail=str(error)) from error

    if challenge is None or challenge_record is None:
        raise HTTPException(status_code=404, detail="Challenge not found.")

    answers = [answer.model_dump() for answer in completion.answers]

    try:
        verified_score = verify_completed_answers(challenge, answers)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error

    official_result = record_player_result(
        request.state.player_id,
        challenge_record["id"],
        verified_score,
        app.state.database_path,
    )
    official_score = official_result["score"]
    first_completion = official_result["first_completion"]

    return {
        "challenge_date": normalized_date,
        "verified_score": verified_score,
        "verified_percentage": round(verified_score / TOTAL_QUESTIONS * 100, 2),
        "official_score": official_score,
        "total_questions": TOTAL_QUESTIONS,
        "percentage": round(official_score / TOTAL_QUESTIONS * 100, 2),
        "first_official_completion": first_completion,
        "replay": not first_completion,
        "original_official_score": None if first_completion else official_score,
        "completed_at": official_result["completed_at"],
    }


@app.get("/player/history")
def player_history(request: Request):
    results = load_player_results(
        request.state.player_id,
        app.state.database_path,
    )

    return {
        "results": [
            {
                "challenge_date": result["challenge_date"],
                "official_score": result["score"],
                "total_questions": TOTAL_QUESTIONS,
                "percentage": round(result["score"] / TOTAL_QUESTIONS * 100, 2),
                "completed_at": result["completed_at"],
            }
            for result in results
        ]
    }


@app.get("/archive")
def archive_month(year: int, month: int, request: Request):
    if year < 1 or year > 9999 or month < 1 or month > 12:
        raise HTTPException(status_code=400, detail="Invalid archive month.")

    first_date = date(year, month, 1)

    if year == 9999 and month == 12:
        next_month_date = "9999-12-32"
    elif month == 12:
        next_month_date = date(year + 1, 1, 1)
    else:
        next_month_date = date(year, month + 1, 1)

    archive_entries = load_month_archive(
        request.state.player_id,
        first_date,
        next_month_date,
        app.state.database_path,
    )

    return {
        "year": year,
        "month": month,
        "month_name": month_name[month],
        "days_in_month": monthrange(year, month)[1],
        "today": current_challenge_date(),
        "challenges": [
            {
                "challenge_date": entry["challenge_date"],
                "completed": entry["official_score"] is not None,
                "official_score": entry["official_score"],
                "total_questions": TOTAL_QUESTIONS,
                "percentage": (
                    round(entry["official_score"] / TOTAL_QUESTIONS * 100, 2)
                    if entry["official_score"] is not None
                    else None
                ),
            }
            for entry in archive_entries
        ],
    }
