"""store_channels queries. Lookups by channel id are only used after the webhook secret is verified."""

from uuid import UUID

import asyncpg


async def get_store_channel(conn: asyncpg.Connection, store_id: UUID, channel: str) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        select id, store_id, channel::text as channel, external_id, credentials, is_active
          from store_channels where store_id = $1 and channel = $2::channel_type
        """,
        store_id, channel,
    )


async def get_active_channel(conn: asyncpg.Connection, channel_id: UUID, channel: str) -> asyncpg.Record | None:
    """The channel a webhook is addressed to (its store_id comes from here, never from the payload)."""
    return await conn.fetchrow(
        """
        select id, store_id, external_id, credentials from store_channels
         where id = $1 and channel = $2::channel_type and is_active and credentials is not null
        """,
        channel_id, channel,
    )


async def get_active_by_external_id(conn: asyncpg.Connection, channel: str, external_id: str) -> asyncpg.Record | None:
    """WhatsApp: the store whose number received the message (phone_number_id from Meta's signed payload)."""
    return await conn.fetchrow(
        """
        select id, store_id, external_id, credentials from store_channels
         where channel = $1::channel_type and external_id = $2 and is_active and credentials is not null
        """,
        channel, external_id,
    )


async def external_id_taken(conn: asyncpg.Connection, channel: str, external_id: str, store_id: UUID) -> bool:
    """Is this bot/number already active for another store? (One webhook per bot.)"""
    return await conn.fetchval(
        """
        select exists(select 1 from store_channels
                       where channel = $1::channel_type and external_id = $2 and is_active and store_id <> $3)
        """,
        channel, external_id, store_id,
    )


async def save_channel(conn: asyncpg.Connection, store_id: UUID, channel: str, external_id: str, credentials: str) -> UUID:
    return await conn.fetchval(
        """
        insert into store_channels (store_id, channel, external_id, credentials, is_active)
        values ($1, $2::channel_type, $3, $4, true)
        on conflict (store_id, channel)
        do update set external_id = excluded.external_id, credentials = excluded.credentials, is_active = true
        returning id
        """,
        store_id, channel, external_id, credentials,
    )


async def disable_channel(conn: asyncpg.Connection, store_id: UUID, channel: str) -> None:
    """Deactivate and forget the credentials (the token isn't kept after disconnecting)."""
    await conn.execute(
        "update store_channels set is_active = false, credentials = null where store_id = $1 and channel = $2::channel_type",
        store_id, channel,
    )
