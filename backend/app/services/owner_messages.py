"""The owner's messages to a customer: a free reply (1.12) or an answer to an escalation (2.3).

Two steps so the caller controls the transaction:
1. `store` (inside the caller's transaction, conversation row locked): save the message as
   sender 'owner'. Fails with ChannelUnavailable before saving if the channel can't deliver.
2. `deliver` (after commit): send it on the conversation's own channel. A delivery error is
   logged, not raised: the message is saved and visible in the history either way.
"""

import logging
from uuid import UUID

import asyncpg

from app.channels.base import ChannelAdapter, OutboundMessage
from app.channels.registry import adapter_for
from app.config import Settings
from app.db import conversations as db

log = logging.getLogger(__name__)


class ChannelUnavailable(Exception):
    pass


async def store(conn: asyncpg.Connection, settings: Settings, store_id: UUID, conversation: asyncpg.Record,
                text: str) -> ChannelAdapter:
    adapter = await adapter_for(conn, settings, store_id, conversation["channel"])
    if adapter is None:
        raise ChannelUnavailable(conversation["channel"])
    await db.insert_message(conn, store_id, conversation["id"], "owner", text, {})
    await db.touch_conversation(conn, store_id, conversation["id"])
    return adapter


async def deliver(adapter: ChannelAdapter, conversation: asyncpg.Record, text: str) -> None:
    try:
        await adapter.send(conversation["external_id"], OutboundMessage(text=text))
    except Exception:
        log.exception("owner message not delivered (channel=%s, conversation=%s)",
                      conversation["channel"], conversation["id"])
