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


# --- owner dashboard: conversations page (TASKS.md 1.12) -------------------------------

# view filter -> SQL condition on c (fixed strings, never user input)
VIEWS = {
    "waiting": "c.status = 'waiting_owner'",
    "owner": "c.status <> 'closed' and c.status <> 'waiting_owner' and (c.status = 'owner' or c.bot_paused)",
    "bot": "c.status = 'bot' and not c.bot_paused",
    "closed": "c.status = 'closed'",
}


async def list_conversations(conn, store_id: UUID, *, view: str | None, q: str | None,
                             limit: int, offset: int) -> list[asyncpg.Record]:
    """Newest activity first. q matches the customer's name/phone or any message text (normalized Arabic)."""
    condition = VIEWS.get(view or "", "true")
    return await conn.fetch(
        f"""
        select c.id, c.channel::text as channel, c.status::text as status, c.bot_paused,
               c.last_message_at, c.created_at,
               cu.name as customer_name, cu.phone as customer_phone,
               lm.content as last_message, lm.sender as last_sender,
               (select count(*) from escalations e
                 where e.store_id = c.store_id and e.conversation_id = c.id and e.status = 'open') as open_escalations
          from conversations c
          join customers cu on cu.id = c.customer_id and cu.store_id = c.store_id
          left join lateral (
                select m.content, m.sender::text as sender from messages m
                 where m.store_id = c.store_id and m.conversation_id = c.id
                 order by m.created_at desc, m.id desc limit 1) lm on true
         where c.store_id = $1 and {condition}
           and ($2::text is null
                or position(normalize_ar($2) in normalize_ar(coalesce(cu.name, '') || ' ' || coalesce(cu.phone, ''))) > 0
                or exists (select 1 from messages m
                            where m.store_id = c.store_id and m.conversation_id = c.id
                              and position(normalize_ar($2) in normalize_ar(m.content)) > 0))
         order by c.last_message_at desc, c.id
         limit $3 offset $4
        """,
        store_id, (q or "").strip() or None, limit, offset,
    )


async def get_conversation(conn, store_id: UUID, conversation_id: UUID, *, lock: bool = False) -> asyncpg.Record | None:
    """One conversation with its customer. lock=True takes the same row lock as the engine's respond step."""
    return await conn.fetchrow(
        f"""
        select c.id, c.channel::text as channel, c.status::text as status, c.bot_paused, c.bot_state,
               c.last_message_at, c.created_at,
               cu.name as customer_name, cu.phone as customer_phone, cu.external_id
          from conversations c
          join customers cu on cu.id = c.customer_id and cu.store_id = c.store_id
         where c.id = $1 and c.store_id = $2
         {"for update of c" if lock else ""}
        """,
        conversation_id, store_id,
    )


async def conversation_messages(conn, store_id: UUID, conversation_id: UUID, limit: int = 500) -> list[asyncpg.Record]:
    """Oldest first (the latest `limit` messages)."""
    return await conn.fetch(
        """
        select * from (
          select id, sender::text as sender, content, created_at from messages
           where store_id = $1 and conversation_id = $2
           order by created_at desc, id desc limit $3
        ) recent order by created_at, id
        """,
        store_id, conversation_id, limit,
    )


async def conversation_escalations(conn, store_id: UUID, conversation_id: UUID) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        select id, rule, question, status::text as status, owner_answer, save_to_kb, created_at from escalations
         where store_id = $1 and conversation_id = $2
         order by created_at desc
        """,
        store_id, conversation_id,
    )
