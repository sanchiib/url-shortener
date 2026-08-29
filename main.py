#!/usr/bin/env python3
"""
main.py - a tiny, self-contained URL shortener.

Subcommands:
    shorten <url> [--alias CODE]   create a short code for a URL
    resolve <code> [--open]        look up the URL behind a code
    list [--json]                  show everything stored so far

Storage is a single SQLite file so the tool works the same way from
any directory and survives across runs without any extra services.
No third-party packages, no network calls, no external shortening APIs.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import string
import sys
import webbrowser
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_DB_PATH = Path.home() / ".urlshortener" / "shortener.db"

# Codes are base62 encodings of the row's autoincrement id, offset so
# that even the first entry produces something that looks like a real
# short code instead of "1", "2", "3", ...
BASE62_ALPHABET = string.digits + string.ascii_lowercase + string.ascii_uppercase
CODE_OFFSET = 46_656  # 62^3, i.e. guarantees at least 4 base62 digits

ALIAS_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")


class ShortenerError(Exception):
    """Raised for any user-facing failure (bad input, missing code, ...)."""


# --------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------

class Storage:
    """Thin wrapper around the sqlite3 connection and schema."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS urls (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                code       TEXT UNIQUE NOT NULL,
                long_url   TEXT NOT NULL,
                created_at TEXT NOT NULL,
                clicks     INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_urls_long_url ON urls(long_url)"
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # -- queries -----------------------------------------------------

    def find_by_url(self, long_url: str) -> sqlite3.Row | None:
        cur = self.conn.execute(
            "SELECT * FROM urls WHERE long_url = ? LIMIT 1", (long_url,)
        )
        return cur.fetchone()

    def find_by_code(self, code: str) -> sqlite3.Row | None:
        cur = self.conn.execute("SELECT * FROM urls WHERE code = ?", (code,))
        return cur.fetchone()

    def code_exists(self, code: str) -> bool:
        return self.find_by_code(code) is not None

    def insert_with_alias(self, long_url: str, alias: str) -> sqlite3.Row:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.conn:
            self.conn.execute(
                "INSERT INTO urls (code, long_url, created_at, clicks) "
                "VALUES (?, ?, ?, 0)",
                (alias, long_url, now),
            )
        return self.find_by_code(alias)

    def insert_generated(self, long_url: str) -> sqlite3.Row:
        """Insert a row first to get an id, then derive+store its code.

        Two statements instead of one because the code is a function of
        the id sqlite assigns, which we don't know until after the insert.
        """
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO urls (code, long_url, created_at, clicks) "
                "VALUES (?, ?, ?, 0)",
                ("", long_url, now),
            )
            new_id = cur.lastrowid
            code = encode_base62(new_id + CODE_OFFSET)
            self.conn.execute(
                "UPDATE urls SET code = ? WHERE id = ?", (code, new_id)
            )
        return self.find_by_code(code)

    def increment_clicks(self, code: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE urls SET clicks = clicks + 1 WHERE code = ?", (code,)
            )

    def all_rows(self):
        cur = self.conn.execute("SELECT * FROM urls ORDER BY id ASC")
        return cur.fetchall()


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def encode_base62(num: int) -> str:
    if num == 0:
        return BASE62_ALPHABET[0]
    digits = []
    base = len(BASE62_ALPHABET)
    while num:
        num, rem = divmod(num, base)
        digits.append(BASE62_ALPHABET[rem])
    return "".join(reversed(digits))


def validate_url(raw_url: str) -> str:
    """Return a normalized URL or raise ShortenerError if it's not usable."""
    raw_url = raw_url.strip()
    if not raw_url:
        raise ShortenerError("URL cannot be empty.")

    parsed = urlparse(raw_url)
    if parsed.scheme not in ("http", "https"):
        raise ShortenerError(
            f"Unsupported or missing scheme in '{raw_url}'. "
            "URLs must start with http:// or https://."
        )
    if not parsed.netloc:
        raise ShortenerError(f"'{raw_url}' doesn't look like a valid URL.")
    return raw_url


def validate_alias(alias: str) -> str:
    alias = alias.strip()
    if not ALIAS_RE.match(alias):
        raise ShortenerError(
            "Alias must be 3-32 characters long and contain only letters, "
            "digits, hyphens, or underscores."
        )
    return alias


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_shorten(store: Storage, args: argparse.Namespace) -> None:
    long_url = validate_url(args.url)

    if args.alias:
        alias = validate_alias(args.alias)
        existing = store.find_by_code(alias)
        if existing is not None:
            raise ShortenerError(
                f"Alias '{alias}' is already taken "
                f"(points to {existing['long_url']})."
            )
        row = store.insert_with_alias(long_url, alias)
        print(row["code"])
        return

    # No alias requested: if this exact URL was already shortened before,
    # hand back the existing code instead of creating a duplicate entry.
    existing = store.find_by_url(long_url)
    if existing is not None:
        print(existing["code"])
        print(
            f"(note: '{long_url}' was already shortened as "
            f"'{existing['code']}')",
            file=sys.stderr,
        )
        return

    row = store.insert_generated(long_url)
    print(row["code"])


def cmd_resolve(store: Storage, args: argparse.Namespace) -> None:
    row = store.find_by_code(args.code)
    if row is None:
        raise ShortenerError(f"No URL found for code '{args.code}'.")

    store.increment_clicks(args.code)
    print(row["long_url"])

    if args.open:
        webbrowser.open(row["long_url"])


def cmd_list(store: Storage, args: argparse.Namespace) -> None:
    rows = store.all_rows()
    if not rows:
        print("No URLs have been shortened yet.")
        return

    if args.json:
        import json

        payload = [
            {
                "code": r["code"],
                "url": r["long_url"],
                "created_at": r["created_at"],
                "clicks": r["clicks"],
            }
            for r in rows
        ]
        print(json.dumps(payload, indent=2))
        return

    code_width = max(len(r["code"]) for r in rows)
    code_width = max(code_width, len("CODE"))
    print(f"{'CODE':<{code_width}}  {'CLICKS':>6}  {'CREATED':<19}  URL")
    for r in rows:
        print(
            f"{r['code']:<{code_width}}  {r['clicks']:>6}  "
            f"{r['created_at']:<19}  {r['long_url']}"
        )


# --------------------------------------------------------------------------
# CLI wiring
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="A local, dependency-free URL shortener.",
    )
    parser.add_argument(
        "--db",
        dest="db_path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"path to the sqlite database (default: {DEFAULT_DB_PATH})",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    p_shorten = subparsers.add_parser("shorten", help="shorten a URL")
    p_shorten.add_argument("url", help="the long URL to shorten")
    p_shorten.add_argument(
        "--alias", help="use a custom short code instead of an auto-generated one"
    )
    p_shorten.set_defaults(func=cmd_shorten)

    p_resolve = subparsers.add_parser("resolve", help="look up a short code")
    p_resolve.add_argument("code", help="the short code to resolve")
    p_resolve.add_argument(
        "--open", action="store_true", help="open the URL in the default browser"
    )
    p_resolve.set_defaults(func=cmd_resolve)

    p_list = subparsers.add_parser("list", help="list all stored mappings")
    p_list.add_argument(
        "--json", action="store_true", help="print the list as JSON instead of a table"
    )
    p_list.set_defaults(func=cmd_list)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    with Storage(args.db_path) as store:
        try:
            args.func(store, args)
        except ShortenerError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
