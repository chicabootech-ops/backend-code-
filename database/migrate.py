#!/usr/bin/env python3
"""Apply Chic A Boo SQL migrations in order."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg2

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise SystemExit("DATABASE_URL is required")
    # asyncpg/SQLAlchemy URLs are not accepted by psycopg2
    return (
        url.replace("postgresql+asyncpg://", "postgresql://")
        .replace("postgres://", "postgresql://")
        .split("?")[0]
    )


def ensure_migrations_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.schema_migrations (
                version     TEXT PRIMARY KEY,
                applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
    conn.commit()


def applied_versions(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT version FROM public.schema_migrations ORDER BY version;")
        return {row[0] for row in cur.fetchall()}


def apply_migration(conn, path: Path) -> None:
    sql_text = path.read_text(encoding="utf-8")
    version = path.stem
    print(f"Applying {version} ...")
    with conn.cursor() as cur:
        cur.execute(sql_text)
        cur.execute(
            "INSERT INTO public.schema_migrations (version) VALUES (%s);",
            (version,),
        )
    conn.commit()
    print(f"Applied {version}")


def migrate(target: str | None = None) -> None:
    database_url = get_database_url()
    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))

    if not migration_files:
        raise SystemExit(f"No migration files found in {MIGRATIONS_DIR}")

    with psycopg2.connect(database_url) as conn:
        conn.autocommit = False
        ensure_migrations_table(conn)
        done = applied_versions(conn)

        for path in migration_files:
            version = path.stem
            if version in done:
                continue
            if target and version > target:
                break
            apply_migration(conn, path)

    print("Migrations complete.")


def status() -> None:
    database_url = get_database_url()
    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))

    with psycopg2.connect(database_url) as conn:
        ensure_migrations_table(conn)
        done = applied_versions(conn)

    for path in migration_files:
        version = path.stem
        mark = "applied" if version in done else "pending"
        print(f"{version}: {mark}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Chic A Boo database migrations")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", help="Apply pending migrations")
    migrate_parser = sub.add_parser("up", help="Apply pending migrations (alias)")
    migrate_parser.add_argument("--to", dest="target", help="Stop after this migration version")

    sub.add_parser("status", help="Show migration status")

    args = parser.parse_args()

    if args.command in ("migrate", "up"):
        migrate(getattr(args, "target", None))
    elif args.command == "status":
        status()


if __name__ == "__main__":
    main()
