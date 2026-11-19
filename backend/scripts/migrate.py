"""Apply SQL migrations from supabase/migrations/ to the database in DATABASE_URL.

Usage (from backend/):
    uv run python scripts/migrate.py           # apply pending migrations
    uv run python scripts/migrate.py --status  # list applied / pending

Each file runs in its own transaction and is recorded in public.schema_migrations,
so re-running only applies new files.
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = BACKEND_DIR.parent / "supabase" / "migrations"

TRACKING_TABLE_SQL = """
create table if not exists public.schema_migrations (
  filename   text primary key,
  applied_at timestamptz not null default now()
);
-- RLS on with no policies: invisible to anon/authenticated through the Supabase API.
alter table public.schema_migrations enable row level security;
"""


def migration_files() -> list[Path]:
    """Numbered .sql files in apply order (0001_..., 0002_...)."""
    return sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql"))


async def connect() -> asyncpg.Connection:
    load_dotenv(BACKEND_DIR / ".env")
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set. Add it to backend/.env (see .env.example).")
    try:
        # statement_cache_size=0 keeps it compatible with Supabase's pooler.
        return await asyncpg.connect(url, statement_cache_size=0)
    except ValueError:
        # Don't echo the parser error: it can contain parts of the password.
        sys.exit(
            "DATABASE_URL could not be parsed. If the password has special characters "
            "(# / ? @ : %), percent-encode them, e.g. # -> %23, / -> %2F, ? -> %3F, @ -> %40."
        )


async def applied_set(conn: asyncpg.Connection) -> set[str]:
    rows = await conn.fetch("select filename from public.schema_migrations")
    return {r["filename"] for r in rows}


async def main(status_only: bool) -> None:
    conn = await connect()
    try:
        await conn.execute(TRACKING_TABLE_SQL)
        done = await applied_set(conn)
        pending = [f for f in migration_files() if f.name not in done]

        if status_only:
            for f in migration_files():
                print(f"{'applied' if f.name in done else 'pending'}  {f.name}")
            return

        if not pending:
            print("Nothing to apply. Database is up to date.")
            return

        for f in pending:
            sql = f.read_text(encoding="utf-8")
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "insert into public.schema_migrations (filename) values ($1)", f.name
                )
            print(f"applied  {f.name}")
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--status", action="store_true", help="list migrations without applying")
    args = parser.parse_args()
    asyncio.run(main(args.status))
