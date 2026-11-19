"""Store queries."""

from uuid import UUID

import asyncpg


async def get_store_id_for_owner(conn: asyncpg.Connection, owner_id: UUID) -> UUID | None:
    """The store owned by this auth user, or None if they haven't created one yet."""
    return await conn.fetchval("select id from stores where owner_id = $1", owner_id)
