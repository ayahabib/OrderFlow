"""Conversation engine entry point (ARCHITECTURE.md section 4).

Two steps, so webhooks can answer fast (hard rule 11):
1. `ingest` (inside the webhook request): find/create the customer and conversation,
   store the message once (duplicates are ignored). Cheap, and the message is safe
   in the DB before we return 200.
2. `respond` (background task): run the order flow (engine/order_flow.py, which applies
   the forced-escalation rules first), send the reply, and alert the owner on Telegram
   when the conversation was handed over. Task 2.2 adds Q&A.
"""

import json
import logging
from dataclasses import dataclass
from uuid import UUID

import asyncpg

from app.channels.base import ChannelAdapter, InboundMessage
from app.db import conversations as db
from app.engine import order_flow
from app.llm.client import LLM
from app.llm.embeddings import Embedder
from app.services import notifications

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


async def respond(
    conn: asyncpg.Connection, store_id: UUID, conversation_id: UUID, to: str, text: str,
    adapter: ChannelAdapter, llm: LLM | None,
    notifier: notifications.Notifier | None = None, dashboard_url: str = "",
    embedder: Embedder | None = None,
) -> None:
    """Background step: run the order flow, store the reply and the new state, deliver it.

    On escalation the owner's alert goes out after the transaction commits, so a slow or
    failing Telegram call never holds the conversation lock or rolls the escalation back.
    """
    async with conn.transaction():
        # Lock the conversation: messages of one customer are handled one at a time,
        # so each step sees the state the previous one saved.
        row = await conn.fetchrow(
            """
            select c.bot_paused or c.status in ('owner', 'waiting_owner') as paused, c.bot_state, c.customer_id,
                   cu.phone, s.currency, s.large_order_item_qty, s.large_order_total_qty, s.large_order_amount
              from conversations c
              join customers cu on cu.id = c.customer_id and cu.store_id = c.store_id
              join stores s on s.id = c.store_id
             where c.id = $1 and c.store_id = $2
               for update of c
            """,
            conversation_id, store_id,
        )
        # Re-check: the owner may have paused the bot between ingest and now.
        if row is None or row["paused"]:
            return

        raw_state = json.loads(row["bot_state"]) if isinstance(row["bot_state"], str) else row["bot_state"]
        ctx = order_flow.Context(
            conn=conn, store_id=store_id, conversation_id=conversation_id, customer_id=row["customer_id"],
            llm=llm, currency=row["currency"],
            large_item_qty=row["large_order_item_qty"], large_total_qty=row["large_order_total_qty"],
            large_amount=row["large_order_amount"], customer_phone=row["phone"], embedder=embedder,
        )
        outcome = await order_flow.handle(ctx, order_flow.BotState.load(raw_state), text)
        reply = outcome.reply

        await conn.execute(
            "update conversations set bot_state = $3::jsonb where id = $1 and store_id = $2",
            conversation_id, store_id, outcome.state.model_dump_json(),
        )
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

    if outcome.escalated:
        try:
            await notifications.notify_escalation(conn, notifier, store_id, conversation_id, dashboard_url)
        except Exception:
            log.exception("owner alert failed (conversation=%s)", conversation_id)
