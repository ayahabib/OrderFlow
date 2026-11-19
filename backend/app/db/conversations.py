"""Customers, conversations and messages. Every query filters by store_id."""

import json
from uuid import UUID

import asyncpg


async def upsert_customer(conn, store_id: UUID, channel: str, external_id: str, name: str | None) -> UUID:
    return await conn.fetchval(
        """
        insert into customers (store_id, channel, external_id, name)
        values ($1, $2::channel_type, $3, $4)
        on conflict (store_id, channel, external_id)
        do update set name = coalesce(excluded.name, customers.name)
        returning id
        """,
        store_id, channel, external_id, name,
    )


async def open_conversation(conn, store_id: UUID, customer_id: UUID, channel: str) -> asyncpg.Record:
    """The customer's latest conversation that isn't closed, or a new one."""
    row = await conn.fetchrow(
        """
        select id, status::text as status, bot_paused, bot_state
          from conversations
         where store_id = $1 and customer_id = $2 and status <> 'closed'
         order by last_message_at desc
         limit 1
        """,
        store_id, customer_id,
    )
    if row:
        return row
    return await conn.fetchrow(
        """
        insert into conversations (store_id, customer_id, channel)
        values ($1, $2, $3::channel_type)
        returning id, status::text as status, bot_paused, bot_state
        """,
        store_id, customer_id, channel,
    )


async def insert_customer_message(
    conn, store_id: UUID, conversation_id: UUID, text: str, metadata: dict
) -> UUID | None:
    """Store an inbound message. None if this external_message_id was already stored (duplicate webhook)."""
    return await conn.fetchval(
        """
        insert into messages (store_id, conversation_id, sender, content, metadata, created_at)
        values ($1, $2, 'customer', $3, $4::jsonb, clock_timestamp())
        on conflict (store_id, (metadata ->> 'external_message_id'))
          where sender = 'customer' and metadata ? 'external_message_id'
        do nothing
        returning id
        """,
        store_id, conversation_id, text, json.dumps(metadata, ensure_ascii=False),
    )


# clock_timestamp(), not now(): now() is fixed per transaction, so two messages saved
# in one transaction would get the same time and could sort in the wrong order.


async def insert_message(conn, store_id: UUID, conversation_id: UUID, sender: str, text: str, metadata: dict) -> UUID:
    return await conn.fetchval(
        """
        insert into messages (store_id, conversation_id, sender, content, metadata, created_at)
        values ($1, $2, $3::message_sender, $4, $5::jsonb, clock_timestamp())
        returning id
        """,
        store_id, conversation_id, sender, text, json.dumps(metadata, ensure_ascii=False),
    )


async def touch_conversation(conn, store_id: UUID, conversation_id: UUID) -> None:
    await conn.execute(
        "update conversations set last_message_at = now() where id = $1 and store_id = $2", conversation_id, store_id
    )


async def find_customer(conn, store_id: UUID, channel: str, external_id: str) -> UUID | None:
    return await conn.fetchval(
        "select id from customers where store_id = $1 and channel = $2::channel_type and external_id = $3",
        store_id, channel, external_id,
    )


async def customer_messages(conn, store_id: UUID, customer_id: UUID, limit: int = 200) -> list[dict]:
    """The customer's messages across their conversations, oldest first."""
    rows = await conn.fetch(
        """
        select * from (
          select m.id, m.sender::text as sender, m.content, m.metadata, m.created_at
            from messages m
            join conversations c on c.id = m.conversation_id and c.store_id = m.store_id
           where m.store_id = $1 and c.customer_id = $2
           order by m.created_at desc, m.id desc
           limit $3
        ) recent
        order by created_at, id
        """,
        store_id, customer_id, limit,
    )
    result = []
    for r in rows:
        data = dict(r)
        if isinstance(data["metadata"], str):
            data["metadata"] = json.loads(data["metadata"])
        result.append(data)
    return result
