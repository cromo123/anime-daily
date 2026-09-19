"""Developer-only draft and approval workflow for future challenges."""

from argparse import ArgumentParser
from datetime import date, datetime, timedelta, timezone

from challenge import load_stored_challenge
from daily_challenge_flow import DailyChallengeFlow
from database import (
    DATABASE_PATH,
    delete_draft_challenge,
    list_challenge_runs,
    load_challenge_record,
    load_series_display_roots,
    set_challenge_publication_state,
)


def _parse_date(value):
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError("Dates must use YYYY-MM-DD format.") from error


def _run_draft_generation(challenge_date):
    flow = DailyChallengeFlow.for_date(
        challenge_date.isoformat(), publication_state="draft"
    )
    flow.kickoff()
    record = load_challenge_record(
        challenge_date.isoformat(), DATABASE_PATH, include_drafts=True
    )
    if record is None or record["publication_state"] != "draft":
        raise RuntimeError("Generation did not produce a draft challenge.")
    return record


def replenish(days_ahead=7, reference_date=None):
    """Ensure an approved challenge exists for today through the future buffer."""
    if days_ahead < 0:
        raise ValueError("--days-ahead must be zero or greater.")

    if reference_date is None:
        reference_date = datetime.now(timezone.utc).date()

    unresolved = []
    for offset in range(days_ahead + 1):
        challenge_date = reference_date + timedelta(days=offset)
        date_text = challenge_date.isoformat()
        existing = load_challenge_record(
            date_text, DATABASE_PATH, include_drafts=True
        )

        if existing is not None and existing["publication_state"] == "approved":
            print(f"{challenge_date}: already approved")
            continue

        if existing is not None and existing["publication_state"] == "draft":
            try:
                set_challenge_publication_state(
                    date_text, "approved", DATABASE_PATH
                )
                print(f"{challenge_date}: existing draft approved")
            except Exception as error:
                unresolved.append(challenge_date)
                print(f"{challenge_date}: replenishment failed: {error}")
            continue

        try:
            _run_draft_generation(challenge_date)
            set_challenge_publication_state(
                date_text, "approved", DATABASE_PATH
            )
            print(f"{challenge_date}: generated and approved")
        except Exception as error:
            # A concurrent invocation may have completed this date while this
            # process was curating it. Re-read the exact date before reporting
            # a failure; approved challenges are never overwritten.
            try:
                concurrent = load_challenge_record(
                    date_text, DATABASE_PATH, include_drafts=True
                )
                if (
                    concurrent is not None
                    and concurrent["publication_state"] == "approved"
                ):
                    print(f"{challenge_date}: already approved")
                    continue
            except Exception:
                pass
            unresolved.append(challenge_date)
            print(f"{challenge_date}: replenishment failed: {error}")

    if unresolved:
        dates = ", ".join(day.isoformat() for day in unresolved)
        raise RuntimeError(f"Unapproved target dates remain: {dates}")


def generate(start, days):
    if days < 1:
        raise ValueError("--days must be at least 1.")
    for offset in range(days):
        challenge_date = start + timedelta(days=offset)
        existing = load_challenge_record(
            challenge_date.isoformat(), DATABASE_PATH, include_drafts=True
        )
        if existing is not None:
            print(
                f"{challenge_date}: skipped existing "
                f"{existing['publication_state']} challenge {existing['id']}"
            )
            continue
        try:
            record = _run_draft_generation(challenge_date)
            print(f"{challenge_date}: draft challenge {record['id']} generated")
        except Exception as error:
            print(f"{challenge_date}: generation failed: {error}")


def list_challenges():
    for row in list_challenge_runs(DATABASE_PATH):
        print(
            f"{row['challenge_date']}  #{row['id']}  "
            f"{row['publication_state']}"
        )


def inspect_challenge(challenge_date):
    record = load_challenge_record(
        challenge_date.isoformat(), DATABASE_PATH, include_drafts=True
    )
    if record is None:
        raise RuntimeError(f"No challenge stored for {challenge_date}.")
    challenge = load_stored_challenge(
        challenge_date.isoformat(), DATABASE_PATH, include_drafts=True
    )
    print(
        f"{challenge_date}  challenge #{record['id']}  "
        f"{record['publication_state']}"
    )
    roots = load_series_display_roots(
        [
            anime["mal_id"]
            for category in challenge
            if category["name"] == "More Episodes"
            for anime in category["anime"]
        ],
        DATABASE_PATH,
    )
    for category in challenge:
        print(f"\n{category['name']}")
        for position, anime in enumerate(category["anime"], start=1):
            metric = category["metric"]
            display = roots.get(anime["mal_id"]) if category["name"] == "More Episodes" else None
            display_text = (
                f"display root MAL {display['mal_id']} | {display['title']}"
                if display
                else f"display title {anime['title']}"
            )
            print(
                f"{position}. MAL {anime['mal_id']} | {display_text} | "
                f"{metric}={anime.get(metric)}"
            )


def approve(challenge_date):
    record = load_challenge_record(
        challenge_date.isoformat(), DATABASE_PATH, include_drafts=True
    )
    if record is None:
        raise RuntimeError(f"No challenge stored for {challenge_date}.")
    if record["publication_state"] == "approved":
        print(f"{challenge_date}: already approved")
        return
    if set_challenge_publication_state(
        challenge_date.isoformat(), "approved", DATABASE_PATH
    ):
        print(f"{challenge_date}: approved")


def regenerate(challenge_date):
    record = load_challenge_record(
        challenge_date.isoformat(), DATABASE_PATH, include_drafts=True
    )
    if record is None:
        raise RuntimeError(f"No draft exists for {challenge_date}.")
    if record["publication_state"] == "approved":
        raise RuntimeError("Approved challenges cannot be regenerated.")
    delete_draft_challenge(challenge_date.isoformat(), DATABASE_PATH)
    record = _run_draft_generation(challenge_date)
    print(f"{challenge_date}: replaced with draft challenge {record['id']}")


def main():
    parser = ArgumentParser(description="Manage AniMoredle future challenge drafts.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate_parser = subparsers.add_parser("generate")
    generate_parser.add_argument("--start", required=True, type=_parse_date)
    generate_parser.add_argument("--days", required=True, type=int)

    replenish_parser = subparsers.add_parser(
        "replenish",
        help="Ensure an approved rolling UTC challenge buffer exists.",
    )
    replenish_parser.add_argument(
        "--days-ahead", type=int, default=7,
        help="Number of future days after the UTC reference date (default: 7).",
    )
    replenish_parser.add_argument(
        "--today", dest="reference_date", type=_parse_date,
        help="Override the UTC reference date for testing.",
    )

    subparsers.add_parser("list")
    for name in ("inspect", "approve", "regenerate"):
        command_parser = subparsers.add_parser(name)
        command_parser.add_argument("date", type=_parse_date)

    arguments = parser.parse_args()
    if arguments.command == "generate":
        generate(arguments.start, arguments.days)
    elif arguments.command == "replenish":
        replenish(arguments.days_ahead, arguments.reference_date)
    elif arguments.command == "list":
        list_challenges()
    elif arguments.command == "inspect":
        inspect_challenge(arguments.date)
    elif arguments.command == "approve":
        approve(arguments.date)
    else:
        regenerate(arguments.date)


if __name__ == "__main__":
    main()
