"""asyncpg connection pool and the FastAPI dependency that hands out connections.

The backend connects with the database owner role, which bypasses RLS.
Every query must filter by store_id in code (CLAUDE.md hard rule 3).
"""

from collections.abc import AsyncIterator

import asyncpg
from fastapi import Request

from app.config import Settings


async def create_pool(settings: Settings) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        settings.database_url.get_secret_value(),
        min_size=1,
        max_size=5,
        statement_cache_size=0,  # compatible with Supabase's pooler
        command_timeout=30,
    )


def get_db_factory(request: Request):
    """For background tasks: a callable returning `async with`-able connections.

    Background tasks run after the response, when the request's own connection
    (get_db) has already gone back to the pool, so they borrow a fresh one.
    """
    return request.app.state.db_pool.acquire


async def get_db(request: Request) -> AsyncIterator[asyncpg.Connection]:
    """Borrow a pooled connection for the duration of one request."""
    pool: asyncpg.Pool = request.app.state.db_pool
    async with pool.acquire() as conn:
        yield conn
