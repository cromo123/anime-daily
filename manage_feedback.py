"""Private CLI: access is controlled by database credentials / SQLite permissions."""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

from database import (DATABASE_PATH, load_feedback_submissions,
                      prune_feedback_submissions)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-path", default=str(DATABASE_PATH),
                        help="Optional local SQLite file; otherwise DATABASE_URL is used when set.")
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list", help="Show report summaries, newest first.")
    listing.add_argument("--limit", type=int, default=50)
    listing.add_argument("--kind", choices=("bug", "feedback"))
    detail = commands.add_parser("show", help="Read one report, including optional contact details.")
    detail.add_argument("id")
    pruning = commands.add_parser("prune", help="Preview retention cleanup; --apply deletes only old feedback.")
    pruning.add_argument("--older-than-days", type=int, default=90)
    pruning.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "prune":
        if args.older_than_days < 1:
            parser.error("--older-than-days must be positive.")
        cutoff = (datetime.now(timezone.utc) - timedelta(days=args.older_than_days)).isoformat()
        count = prune_feedback_submissions(cutoff, args.database_path, apply=args.apply)
        print(f"{'Deleted' if args.apply else 'Would delete'} {count} feedback reports older than {cutoff}.")
        return
    reports = load_feedback_submissions(
        args.database_path, limit=args.limit if args.command == "list" else 1,
        kind=getattr(args, "kind", None), submission_id=getattr(args, "id", None),
    )
    if args.command == "list":
        reports = [{key: row[key] for key in ("id", "created_at", "kind", "category", "challenge_date")}
                   for row in reports]
    else:
        for row in reports:
            row.pop("content_hash", None)
    # JSON escapes terminal control characters; reports are never executed as markup.
    print(json.dumps(reports, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Feedback command failed. Check database access and schema initialization.", file=sys.stderr)
        raise SystemExit(1)
