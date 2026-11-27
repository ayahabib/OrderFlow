"""Store queries. Every query is scoped by owner_id or store_id."""

from uuid import UUID

import asyncpg

STORE_COLUMNS = "id, name, phone, currency, phone_country_code, timezone, bot_enabled, created_at"


async def get_store_id_for_owner(conn: asyncpg.Connection, owner_id: UUID) -> UUID | None:
    """The store owned by this auth user, or None if they haven't created one yet."""
    return await conn.fetchval("select id from stores where owner_id = $1", owner_id)


async def get_store(conn: asyncpg.Connection, store_id: UUID) -> asyncpg.Record | None:
    return await conn.fetchrow(f"select {STORE_COLUMNS} from stores where id = $1", store_id)


async def create_store(
    conn: asyncpg.Connection, owner_id: UUID, name: str, phone: str | None, currency: str,
    phone_country_code: str = "970",
) -> asyncpg.Record | None:
    """Create the owner's store. Returns None if the owner already has one (one store per user)."""
    return await conn.fetchrow(
        f"""
        insert into stores (owner_id, name, phone, currency, phone_country_code)
        values ($1, $2, $3, $4, $5)
        on conflict (owner_id) do nothing
        returning {STORE_COLUMNS}
        """,
        owner_id, name, phone, currency, phone_country_code,
    )


# Columns the owner may edit from Settings. Anything else is never written.
EDITABLE_COLUMNS = ("name", "phone", "currency", "phone_country_code", "bot_enabled")  # bot_enabled: 5.5


async def update_store(conn: asyncpg.Connection, store_id: UUID, changes: dict) -> asyncpg.Record | None:
    """Apply a partial update. Only EDITABLE_COLUMNS are allowed."""
    fields = {k: v for k, v in changes.items() if k in EDITABLE_COLUMNS}
    if not fields:
        return await get_store(conn, store_id)
    # Column names come from the fixed whitelist above, values are parameters.
    sets = ", ".join(f"{col} = ${i}" for i, col in enumerate(fields, start=2))
    return await conn.fetchrow(
        f"update stores set {sets} where id = $1 returning {STORE_COLUMNS}",
        store_id, *fields.values(),
    )


async def get_owner_telegram_chat_id(conn: asyncpg.Connection, store_id: UUID) -> int | None:
    return await conn.fetchval("select owner_telegram_chat_id from stores where id = $1", store_id)


async def set_owner_telegram_chat_id(conn: asyncpg.Connection, store_id: UUID, chat_id: int | None) -> None:
    """Set by the link flow (chat id comes from Telegram, never from the request body)."""
    await conn.execute("update stores set owner_telegram_chat_id = $2 where id = $1", store_id, chat_id)
