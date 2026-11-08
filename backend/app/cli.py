"""
Admin command line.

    python -m app.cli migrate                      apply database migrations
    python -m app.cli create-user EMAIL [--name N] [--role admin|authority]
    python -m app.cli set-password EMAIL
    python -m app.cli list-users

Passwords are read with a hidden prompt (never as command-line arguments).
"""
from __future__ import annotations

import argparse
import getpass
import sys

from .env import load_dotenv

load_dotenv()

import os  # noqa: E402

from .auth import AuthError, AuthService  # noqa: E402
from .db import Store  # noqa: E402
from .security import password_problem  # noqa: E402


def _store() -> Store:
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set (nikaasi/.env). Users are only persistent with PostgreSQL.")
    s = Store(url)
    if not s.enabled:
        sys.exit(f"Cannot connect to PostgreSQL: {s.error}")
    return s


def _ask_password() -> str:
    while True:
        pw = getpass.getpass("New password: ")
        if problem := password_problem(pw):
            print(problem)
            continue
        if getpass.getpass("Repeat password: ") != pw:
            print("Passwords do not match")
            continue
        return pw


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    c = sub.add_parser("create-user")
    c.add_argument("email")
    c.add_argument("--name", default="")
    c.add_argument("--role", choices=["admin", "authority"], default="authority")
    sp = sub.add_parser("set-password")
    sp.add_argument("email")
    sub.add_parser("list-users")
    args = p.parse_args(argv)

    store = _store()  # Store() applies migrations on connect
    auth = AuthService(store)
    try:
        if args.cmd == "migrate":
            print("Database is up to date.")
        elif args.cmd == "create-user":
            u = auth.create_user(args.email, args.name, args.role, _ask_password())
            auth.audit(None, "user.created.cli", {"email": u["email"], "role": u["role"]})
            print(f"Created {u['role']} {u['email']} (id {u['id']})")
        elif args.cmd == "set-password":
            user = next((u for u in auth.list_users() if u["email"] == args.email.strip().lower()), None)
            if not user:
                sys.exit("No such user")
            auth.update_user(user["id"], password=_ask_password())
            auth.audit(None, "password.reset.cli", {"email": user["email"]})
            print("Password updated; existing sessions were signed out.")
        elif args.cmd == "list-users":
            for u in auth.list_users():
                print(f"{u['id']:>3}  {u['email']:<32} {u['role']:<10} {'active' if u['active'] else 'disabled'}")
    except AuthError as e:
        sys.exit(e.message)
    finally:
        store.close()


if __name__ == "__main__":
    main()
