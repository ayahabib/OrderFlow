"""Conversation engine entry point (ARCHITECTURE.md section 4).

Two steps, so webhooks can answer fast (hard rule 11):
1. `ingest` (inside the webhook request): find/create the customer and conversation,
   store the message once (duplicates are ignored). Cheap, and the message is safe
   in the DB before we return 200.
2. `respond` (background task): decide the reply and send it. Later tasks plug in
   forced-escalation rules (1.11), intent + order flow (1.6-1.9) and Q&A (2.2).
   For task 1.5 the reply is a fixed acknowledgement (echo).
"""

import logging
from dataclasses import dataclass
from uuid import UUID

import asyncpg

from app.channels.base import ChannelAdapter, InboundMessage, OutboundMessage
from app.db import conversations as db

log = logging.getLogger(__name__)


@dataclass
class Ingested:
    conversation_id: UUID
    message_id: UUID | None  # None = duplicate, already processed
    bot_should_reply: bool

    @property
    def duplicate(self) -> bool:
        return self.message_id is None


async def ingest(conn: asyncpg.Connection, msg: InboundMessage) -> Ingested:
    async with conn.transaction():
        customer_id = await db.upsert_customer(conn, msg.store_id, msg.channel, msg.external_customer_id, msg.customer_name)
        conversation = await db.open_conversation(conn, msg.store_id, customer_id, msg.channel)
        message_id = await db.insert_customer_message(
            conn, msg.store_id, conversation["id"], msg.text,
            {"external_message_id": msg.external_message_id, "channel": msg.channel},
        )
        if message_id is not None:
            await db.touch_conversation(conn, msg.store_id, conversation["id"])

    # Paused by the owner, or the owner is handling it: store the message, no auto-reply (PRD 4.12).
    bot_on = not conversation["bot_paused"] and conversation["status"] not in ("owner", "waiting_owner")
    return Ingested(conversation["id"], message_id, bot_should_reply=message_id is not None and bot_on)


def decide_reply(text: str) -> OutboundMessage:
    """Placeholder until the LLM layer (1.6+): a fixed acknowledgement, never invented content."""
    return OutboundMessage(text=f"وصلتني رسالتك: «{text}». البوت قيد التجهيز، رح نرد عليك قريباً.")


async def respond(
    conn: asyncpg.Connection, store_id: UUID, conversation_id: UUID, to: str, text: str, adapter: ChannelAdapter
) -> None:
    """Background step: build the reply, store it, deliver it."""
    # Re-check: the owner may have paused the bot between ingest and now.
    paused = await conn.fetchval(
        "select bot_paused or status in ('owner', 'waiting_owner') from conversations where id = $1 and store_id = $2",
        conversation_id, store_id,
    )
    if paused is None or paused:
        return

    reply = decide_reply(text)
    await db.insert_message(
        conn, store_id, conversation_id, "bot", reply.text,
        {"buttons": [b.model_dump() for b in reply.buttons]} if reply.buttons else {},
    )
    await db.touch_conversation(conn, store_id, conversation_id)
    try:
        await adapter.send(to, reply)
    except Exception:
        # The reply is stored; delivery problems are logged without the customer's text.
        log.exception("send failed (channel=%s, conversation=%s)", adapter.channel, conversation_id)
