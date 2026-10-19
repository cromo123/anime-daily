import os
import logging
import re
import secrets
import traceback
from calendar import month_name, monthrange
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from xml.etree.ElementTree import Element, SubElement, tostring

from feedback import FeedbackRateLimiter, receive_feedback
from public_pages import (SITE_URL, STATIC_PAGES, archive_detail, archive_index,
                          available_historical_challenges, document, historical_cutoff)

from challenge import (
    CATEGORY_RULES,
    LegacyChallengeError,
    TOTAL_QUESTIONS,
    evaluate_comparison,
    load_stored_challenge,
    serialize_public_challenge,
    validate_public_challenge,
    PublicChallengeValidationError,
    verify_completed_answers,
)
from database import (
    DATABASE_PATH,
    ensure_player,
    load_challenge_record,
    load_comparison_answer_stats,
    load_month_archive,
    load_player_results,
    load_player_answers,
    load_player_result_standing,
    record_player_answer,
    record_player_result,
    load_public_archive_records,
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
DEV_MODE = os.getenv("ANIME_DAILY_DEV_MODE", "").lower() in {
    "1",
    "true",
    "yes",
}
PUBLIC_ARCHIVE_CUTOFF = date(2026, 9, 19)

app = FastAPI(title="AniMoredle")
app.state.database_path = DATABASE_PATH
app.state.player_cookie_secure = PLAYER_COOKIE_SECURE
app.state.playtest = None
app.state.feedback_limiter = FeedbackRateLimiter()
app.mount("/static", StaticFiles(directory=STATIC_DIRECTORY), name="static")
logger = logging.getLogger(__name__)


def log_archive_failure(error, challenge_date=None):
    """Log location/type, never exception text, SQL parameters, or connection URLs."""
    state = getattr(error, "sqlstate", None)
    sqlstate = state if isinstance(state, str) and re.fullmatch(r"[0-9A-Z]{5}", state) else "-"
    frames = " > ".join(
        f"{Path(frame.filename).name}:{frame.name}:{frame.lineno}"
        for frame in traceback.extract_tb(error.__traceback__)[-5:]
    )
    reference = secrets.token_hex(8)
    logger.error("Public archive failure ref=%s date=%s type=%s sqlstate=%s frames=%s",
                 reference, challenge_date or "index", type(error).__name__, sqlstate, frames)
    return reference


@app.middleware("http")
async def indexing_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if (path.startswith(("/challenge/", "/player/", "/dev/"))
            or path in {"/archive", "/feedback/submit", "/docs", "/redoc", "/openapi.json"}
            or path.startswith("/static/") and path.endswith(".html")
            or path == "/" and ("date" in request.query_params or "playtest" in request.query_params)
            or response.status_code >= 400):
        response.headers["X-Robots-Tag"] = "noindex"
    return response


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
    if request.url.path == "/" and request.query_params.get("playtest") == "1":
        return await call_next(request)

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


def current_challenge_date(local_date=None):
    if local_date is not None:
        return parse_challenge_date(local_date, allow_future=True).isoformat()
    return date.today().isoformat()


def parse_challenge_date(challenge_date, allow_future=False):
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

    if not allow_future and requested_date > date.today():
        raise HTTPException(
            status_code=400,
            detail="Future challenges are not available.",
        )

    return requested_date


def require_public_history_date(requested_date):
    """Keep pre-launch challenge rows available internally, but never publicly."""
    if requested_date < PUBLIC_ARCHIVE_CUTOFF:
        raise HTTPException(status_code=404, detail="Challenge not found.")


def load_challenge_for_api(requested_date):
    challenge_date = requested_date.isoformat()

    try:
        # The API must never substitute another stored day for the requested
        # official challenge. Daily generation is handled separately.
        challenge = load_stored_challenge(
            challenge_date,
            app.state.database_path,
        )
    except LegacyChallengeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=500, detail=str(error)) from error

    if challenge is None:
        raise HTTPException(status_code=404, detail="Challenge not found.")

    try:
        validate_public_challenge(
            challenge,
            requested_date,
            app.state.database_path,
        )
    except PublicChallengeValidationError as error:
        raise HTTPException(
            status_code=503,
            detail="The official challenge is unavailable.",
        ) from error

    return challenge


def evaluate_answer_for_date(requested_date, answer, player_id=None):
    challenge_date = requested_date.isoformat()

    try:
        challenge = load_challenge_for_api(requested_date)
        challenge_record = load_challenge_record(
            challenge_date,
            app.state.database_path,
        )
    except LegacyChallengeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=500, detail=str(error)) from error

    if challenge is None:
        raise HTTPException(status_code=404, detail="Challenge not found.")

    existing = None
    if player_id is not None and challenge_record is not None:
        existing = next(
            (
                row for row in load_player_answers(
                    player_id, challenge_record["id"], app.state.database_path
                )
                if row["category"] == answer.category
                and row["comparison_position"] == answer.comparison_position
            ),
            None,
        )

    try:
        result = evaluate_comparison(
            challenge,
            answer.category,
            answer.comparison_position,
            existing["selected_mal_id"] if existing else answer.selected_mal_id,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error

    if existing is not None:
        stats = load_comparison_answer_stats(
            challenge_record["id"], app.state.database_path
        )
        return {
            "challenge_date": challenge_date,
            **result,
            "comparison_stats": next(
                (
                    stat
                    for stat in stats
                    if stat["category"] == answer.category
                    and stat["comparison_position"] == answer.comparison_position
                ),
                None,
            ),
        }

    if player_id is not None:
        record_player_answer(
            player_id,
            challenge_record["id"],
            answer.category,
            answer.comparison_position,
            answer.selected_mal_id,
            result["correct_mal_id"],
            result["correct"],
            app.state.database_path,
        )
    stats = load_comparison_answer_stats(
        challenge_record["id"], app.state.database_path
    ) if challenge_record is not None else []
    return {
        "challenge_date": challenge_date,
        **result,
        "comparison_stats": next(
            (
                stat
                for stat in stats
                if stat["category"] == answer.category
                and stat["comparison_position"] == answer.comparison_position
            ),
            None,
        ),
    }


def challenge_with_progress(request, requested_date, challenge):
    payload = serialize_public_challenge(
        requested_date, challenge, app.state.database_path
    )
    record = load_challenge_record(requested_date.isoformat(), app.state.database_path)
    payload["comparison_stats"] = (
        load_comparison_answer_stats(record["id"], app.state.database_path)
        if record is not None
        else []
    )
    payload["public_history_start"] = PUBLIC_ARCHIVE_CUTOFF.isoformat()
    if getattr(request.state, "player_id", None) is None or record is None:
        return payload
    rows = load_player_answers(request.state.player_id, record["id"], app.state.database_path)
    answers = []
    for row in rows:
        result = evaluate_comparison(
            challenge, row["category"], row["comparison_position"], row["selected_mal_id"]
        )
        answers.append(result)
    keys = {(row["category"], row["comparison_position"]) for row in rows}
    next_comparison = None
    for category in challenge:
        for position in range(1, len(category["anime"])):
            if (category["name"], position) not in keys:
                next_comparison = {
                    "category": category["name"],
                    "comparison_position": position,
                }
                break
        if next_comparison:
            break
    payload["progress"] = {
        "answers": answers,
        "score": sum(result["correct"] for result in answers),
        "next": next_comparison,
        "complete": next_comparison is None and bool(answers),
    }
    return payload


@app.get("/", include_in_schema=False)
def frontend():
    return FileResponse(STATIC_DIRECTORY / "index.html")


@app.get("/about", include_in_schema=False)
def about_page():
    return FileResponse(STATIC_DIRECTORY / "about.html")


@app.get("/privacy", include_in_schema=False)
def privacy_page():
    return FileResponse(STATIC_DIRECTORY / "privacy.html")


@app.get("/contact", include_in_schema=False)
def contact_page():
    return FileResponse(STATIC_DIRECTORY / "contact.html")


@app.get("/ads.txt", include_in_schema=False)
def ads_txt():
    return FileResponse(STATIC_DIRECTORY / "ads.txt", media_type="text/plain")


@app.get("/how-to-play", include_in_schema=False)
@app.get("/methodology", include_in_schema=False)
@app.get("/faq", include_in_schema=False)
@app.get("/feedback", include_in_schema=False)
@app.get("/guides/higher-score", include_in_schema=False)
@app.get("/guides/more-popular", include_in_schema=False)
@app.get("/guides/more-episodes", include_in_schema=False)
@app.get("/guides/more-recent", include_in_schema=False)
def information_page(request: Request):
    return FileResponse(STATIC_DIRECTORY / STATIC_PAGES[request.url.path], media_type="text/html")


@app.post("/feedback/submit", include_in_schema=False)
async def submit_feedback(request: Request):
    return await receive_feedback(request)


@app.get("/past-challenges", response_class=HTMLResponse, include_in_schema=False)
def past_challenges(page: int = Query(default=1, ge=1, le=1000)):
    try:
        return HTMLResponse(archive_index(app.state.database_path, PUBLIC_ARCHIVE_CUTOFF, page))
    except Exception as error:
        log_archive_failure(error)
        return HTMLResponse(document(
            "Past challenges unavailable", "The historical archive is temporarily unavailable.",
            "/past-challenges", '<h1>Past challenges are temporarily unavailable</h1>'
            '<p>Please try again later, or <a href="/">return to the game</a>.</p>', True,
        ), status_code=503)


@app.get("/past-challenges/{challenge_date}", response_class=HTMLResponse, include_in_schema=False)
def past_challenge(challenge_date: str):
    try:
        requested = date.fromisoformat(challenge_date)
        if requested.isoformat() != challenge_date:
            raise ValueError("Invalid date")
    except ValueError:
        requested = None
    try:
        html = archive_detail(requested, app.state.database_path, PUBLIC_ARCHIVE_CUTOFF) if requested else None
    except Exception as error:
        reference = log_archive_failure(error, requested.isoformat() if requested else None)
        return HTMLResponse(document(
            "Challenge recap temporarily unavailable", "This historical recap is temporarily unavailable.",
            "/past-challenges", '<h1>This recap is temporarily unavailable</h1>'
            '<p>Please try again later. This is a temporary service problem, not a missing challenge.</p>'
            f'<p>Reference: {reference}. <a href="/feedback">Report a problem</a>.</p>', True,
        ), status_code=503, headers={"Retry-After": "60"})
    if html is None:
        return HTMLResponse(document(
            "Challenge recap unavailable", "This historical challenge recap is not available.",
            "/past-challenges", '<h1>This recap is not available</h1>'
            '<p>Recaps are published only for available past challenges after the day has ended worldwide. '
            '<a href="/past-challenges">Browse published days</a> or <a href="/">play today</a>.</p>', True,
        ), status_code=404)
    return HTMLResponse(html)


@app.get("/robots.txt", include_in_schema=False)
def robots_txt():
    return Response(
        "User-agent: *\nAllow: /\nDisallow: /challenge/\nDisallow: /archive\n"
        "Disallow: /player/\nDisallow: /dev/\nDisallow: /feedback/submit\n"
        "Disallow: /docs\nDisallow: /redoc\nDisallow: /openapi.json\n"
        f"Sitemap: {SITE_URL}/sitemap.xml\n", media_type="text/plain",
    )


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap_xml():
    paths = [*STATIC_PAGES, "/past-challenges"]
    try:
        records = load_public_archive_records(historical_cutoff(), PUBLIC_ARCHIVE_CUTOFF,
                                              app.state.database_path, limit=100)
        valid = available_historical_challenges(records, app.state.database_path)
        for record, _ in valid:
            paths.append(f'/past-challenges/{record["challenge_date"]}')
    except Exception:
        # Static content remains discoverable during a database outage.
        pass
    root = Element("urlset", xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")
    for path in paths:
        SubElement(SubElement(root, "url"), "loc").text = SITE_URL + path
    return Response(tostring(root, encoding="utf-8", xml_declaration=True), media_type="application/xml")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/challenge/today")
def get_today_challenge(request: Request, local_date: str | None = None):
    requested_date = (
        parse_challenge_date(local_date, allow_future=True)
        if local_date is not None
        else date.today()
    )
    require_public_history_date(requested_date)
    challenge = load_challenge_for_api(requested_date)
    return challenge_with_progress(request, requested_date, challenge)


@app.get("/challenge/{challenge_date}")
def get_dated_challenge(challenge_date: str, request: Request):
    requested_date = parse_challenge_date(challenge_date)
    require_public_history_date(requested_date)
    challenge = load_challenge_for_api(requested_date)
    return challenge_with_progress(request, requested_date, challenge)


@app.post("/challenge/today/answer")
def answer_today_comparison(
    request: Request,
    answer: AnswerRequest,
    local_date: str | None = None,
):
    requested_date = (
        parse_challenge_date(local_date, allow_future=True)
        if local_date is not None
        else date.today()
    )
    require_public_history_date(requested_date)
    return evaluate_answer_for_date(requested_date, answer, request.state.player_id)


@app.post("/challenge/{challenge_date}/answer")
def answer_dated_comparison(challenge_date: str, request: Request, answer: AnswerRequest):
    requested_date = parse_challenge_date(challenge_date, allow_future=True)
    require_public_history_date(requested_date)
    return evaluate_answer_for_date(requested_date, answer, request.state.player_id)


@app.post("/challenge/{challenge_date}/complete")
def complete_challenge(
    challenge_date: str,
    completion: CompletionRequest,
    request: Request,
):
    requested_date = parse_challenge_date(challenge_date, allow_future=True)
    require_public_history_date(requested_date)
    normalized_date = requested_date.isoformat()

    try:
        challenge = load_challenge_for_api(requested_date)
        challenge_record = load_challenge_record(
            normalized_date,
            app.state.database_path,
        )
    except LegacyChallengeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
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
    standing = load_player_result_standing(
        request.state.player_id,
        challenge_record["id"],
        official_score,
        app.state.database_path,
    )

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
        **standing,
        "comparison_stats": load_comparison_answer_stats(
            challenge_record["id"], app.state.database_path
        ),
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
                "total_questions": result["total_questions"],
                "percentage": round(
                    result["score"] / result["total_questions"] * 100, 2
                ),
                "completed_at": result["completed_at"],
            }
            for result in results
            if date.fromisoformat(result["challenge_date"]) >= PUBLIC_ARCHIVE_CUTOFF
        ]
    }


@app.get("/archive")
def archive_month(
    year: int,
    month: int,
    request: Request,
    local_date: str | None = None,
):
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
        minimum_date=PUBLIC_ARCHIVE_CUTOFF,
    )

    return {
        "year": year,
        "month": month,
        "month_name": month_name[month],
        "days_in_month": monthrange(year, month)[1],
        "today": current_challenge_date(local_date),
        "public_history_start": PUBLIC_ARCHIVE_CUTOFF.isoformat(),
        "challenges": [
            {
                "challenge_date": entry["challenge_date"],
                "completed": entry["official_score"] is not None,
                "official_score": entry["official_score"],
                "total_questions": entry["total_questions"],
                "playable": (
                    entry["total_questions"] == TOTAL_QUESTIONS
                    and entry["category_count"] == len(CATEGORY_RULES)
                ),
                "percentage": (
                    round(
                        entry["official_score"] / entry["total_questions"] * 100,
                        2,
                    )
                    if entry["official_score"] is not None
                    else None
                ),
            }
            for entry in archive_entries
        ],
    }


if DEV_MODE:
    def current_playtest(playtest_id=None):
        playtest = app.state.playtest
        if playtest is None or (
            playtest_id is not None and playtest["id"] != playtest_id
        ):
            raise HTTPException(status_code=404, detail="Playtest not found.")
        return playtest


    def public_playtest(playtest):
        return {
            **serialize_public_challenge(
                playtest["challenge_date"],
                playtest["challenge"],
                app.state.database_path,
            ),
            "playtest_id": playtest["id"],
        }


    @app.post("/dev/playtest/generate")
    def generate_playtest():
        from daily_challenge_flow import DailyChallengeFlow

        previous = app.state.playtest
        for _ in range(3):
            flow = DailyChallengeFlow.for_playtest()
            challenge = flow.kickoff()
            if challenge != flow.state.selected_candidate["categories"]:
                raise RuntimeError(
                    "Playtest Flow did not return its selected challenge."
                )
            if previous is None or challenge != previous["challenge"]:
                break
        else:
            raise HTTPException(
                status_code=503,
                detail="Could not generate a different playtest challenge.",
            )

        playtest = {
            "id": secrets.token_urlsafe(16),
            "challenge_date": flow.state.challenge_date,
            "challenge": challenge,
        }
        app.state.playtest = playtest
        return public_playtest(playtest)


    @app.get("/dev/playtest")
    def get_playtest():
        return public_playtest(current_playtest())


    @app.post("/dev/playtest/{playtest_id}/answer")
    def answer_playtest(playtest_id: str, answer: AnswerRequest):
        playtest = current_playtest(playtest_id)
        try:
            result = evaluate_comparison(
                playtest["challenge"],
                answer.category,
                answer.comparison_position,
                answer.selected_mal_id,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=500, detail=str(error)) from error

        return {"playtest_id": playtest_id, **result}
