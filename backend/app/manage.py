"""Admin commands.

    python -m app.manage create-user --email you@firm.com --name "You" --role admin
    python -m app.manage send-reminders [--dry-run] [--interval-days 7]

Uses the same DATABASE_URL and SMTP_* settings as the API. Run
``send-reminders`` from cron or your host's scheduler, e.g. every weekday at 9:00.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys

from .auth import ROLES, auth_from_env
from .notify import sender_from_env, send_reminders
from .service import IntakeService
from .store import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.manage")
    sub = parser.add_subparsers(dest="command", required=True)

    cu = sub.add_parser("create-user", help="Create a sign-in (the first admin, for example)")
    cu.add_argument("--email", required=True)
    cu.add_argument("--name", default="")
    cu.add_argument("--role", choices=ROLES, required=True)
    cu.add_argument("--client-id", help="Required for client users")

    sr = sub.add_parser("send-reminders", help="Email clients who still have documents missing")
    sr.add_argument("--dry-run", action="store_true", help="Show who would be emailed, send nothing")
    sr.add_argument("--interval-days", type=int, default=7, help="Skip clients reminded more recently than this")

    args = parser.parse_args(argv)
    store = Store(os.getenv("DATABASE_URL") or os.getenv("DATABASE_PATH", ":memory:"))
    try:
        if args.command == "create-user":
            password = os.getenv("NEW_USER_PASSWORD") or getpass.getpass("Password: ")
            try:
                user = auth_from_env(store).create_user(args.email, args.name, args.role, password, args.client_id)
            except ValueError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
            store.log("cli", "user_created", detail=f"{user.email} ({user.role})")
            print(f"Created {user.role} {user.email}")
            return 0
        results = send_reminders(IntakeService(store), sender_from_env(), "scheduler",
                                 dry_run=args.dry_run, min_interval_days=args.interval_days)
        print(json.dumps(results, indent=2))
        return 1 if any(r["status"] == "failed" for r in results) else 0
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
