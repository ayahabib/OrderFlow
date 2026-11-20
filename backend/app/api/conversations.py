"""Conversations page: list, history, pause/resume the bot, owner reply (TASKS.md 1.12, PRD 4.12).

Who answers a conversation:
- bot_paused = true  -> the owner paused the bot by hand.
- status 'waiting_owner' -> the bot escalated and stopped.
- status 'owner' -> the owner replied; the bot stays quiet until resumed.
Resume clears all three. The owner picks whether the bot keeps the open order draft
(e.g. the customer only needed one answer) or starts fresh (the owner settled it in chat).
Confirmed orders are never touched. Answering escalations + "save for bot" is task 2.3.

Every query filters by the caller's store_id (hard rule 3). Pause/resume/reply lock the
conversation row like the engine does, so they never interleave with a bot reply.
"""

import json
import logging
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, StringConstraints

from app.api.auth import get_current_store_id
from app.channels.base import OutboundMessage
from app.channels.registry import adapter_for
from app.db import conversations as db
from app.db.pool import get_db
from app.engine.order_flow import BotState

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/conversations", tags=["conversations"])

View = Literal["waiting", "owner", "bot", "closed"]
# What the page shows: who is answering right now.
Mode = Literal["bot", "paused", "waiting_owner", "owner", "closed"]

NOT_FOUND = "المحادثة مش موجودة"
CLOSED = "المحادثة مسكّرة"


def _mode(status_: str, bot_paused: bool) -> Mode:
    if status_ == "closed":
        return "closed"
    if bot_paused:
        return "paused"
    return status_  # bot | waiting_owner | owner


class ConversationSummary(BaseModel):
    id: UUID
    channel: str
    mode: Mode
    customer_name: str | None
    customer_phone: str | None
    last_message: str | None
    last_sender: str | None
    open_escalations: int
    last_message_at: datetime


class MessageOut(BaseModel):
    id: UUID
    sender: str
    content: str
    created_at: datetime


class EscalationOut(BaseModel):
    id: UUID
    rule: str | None
    question: str
    status: str
    created_at: datetime


class DraftItemOut(BaseModel):
    product_name: str
    variant_name: str
    quantity: int


class DraftOut(BaseModel):
    """The order the bot was collecting (no prices: those are computed only at the summary)."""
    items: list[DraftItemOut]
    unresolved: int       # items mentioned but not matched to a variant yet
    address: str | None
    zone_name: str | None
    phone: str | None
    waiting_for: str | None  # the bot's open question kind, e.g. "address"


class ConversationDetail(BaseModel):
    id: UUID
    channel: str
    mode: Mode
    customer_name: str | None
    customer_phone: str | None
    can_reply: bool          # the channel can deliver an owner reply
    draft: DraftOut | None
    escalations: list[EscalationOut]
    messages: list[MessageOut]


class ReplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class ResumeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    keep_order: bool = False


def _draft(raw) -> DraftOut | None:
    state = BotState.load(json.loads(raw) if isinstance(raw, str) else raw)
    if not (state.items or state.queue or state.pending):
        return None
    return DraftOut(
        items=[DraftItemOut(product_name=i.product_name, variant_name=i.variant_name, quantity=i.quantity) for i in state.items],
        unresolved=len(state.queue) + (1 if state.pending and state.pending.item else 0),
        address=state.address, zone_name=state.zone_name, phone=state.phone,
        waiting_for=state.pending.kind if state.pending else None,
    )


async def _detail(conn: asyncpg.Connection, store_id: UUID, conversation_id: UUID) -> ConversationDetail:
    row = await db.get_conversation(conn, store_id, conversation_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return ConversationDetail(
        id=row["id"], channel=row["channel"], mode=_mode(row["status"], row["bot_paused"]),
        customer_name=row["customer_name"], customer_phone=row["customer_phone"],
        can_reply=adapter_for(row["channel"]) is not None and row["status"] != "closed",
        draft=_draft(row["bot_state"]),
        escalations=[EscalationOut(**dict(e)) for e in await db.conversation_escalations(conn, store_id, conversation_id)],
        messages=[MessageOut(**dict(m)) for m in await db.conversation_messages(conn, store_id, conversation_id)],
    )


async def _locked(conn: asyncpg.Connection, store_id: UUID, conversation_id: UUID) -> asyncpg.Record:
    row = await db.get_conversation(conn, store_id, conversation_id, lock=True)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    if row["status"] == "closed":
        raise HTTPException(status.HTTP_409_CONFLICT, detail=CLOSED)
    return row


@router.get("", response_model=list[ConversationSummary])
async def list_conversations(
    view: View | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> list[ConversationSummary]:
    rows = await db.list_conversations(conn, store_id, view=view, q=q, limit=limit, offset=offset)
    return [ConversationSummary(**{**dict(r), "mode": _mode(r["status"], r["bot_paused"])}) for r in rows]


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: UUID,
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> ConversationDetail:
    return await _detail(conn, store_id, conversation_id)


@router.post("/{conversation_id}/pause", response_model=ConversationDetail)
async def pause_bot(
    conversation_id: UUID,
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> ConversationDetail:
    async with conn.transaction():
        await _locked(conn, store_id, conversation_id)
        await conn.execute("update conversations set bot_paused = true where id = $1 and store_id = $2",
                           conversation_id, store_id)
    return await _detail(conn, store_id, conversation_id)


@router.post("/{conversation_id}/resume", response_model=ConversationDetail)
async def resume_bot(
    conversation_id: UUID,
    body: ResumeIn | None = None,
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> ConversationDetail:
    keep_order = body.keep_order if body else False
    async with conn.transaction():
        await _locked(conn, store_id, conversation_id)
        await conn.execute(
            f"""
            update conversations
               set bot_paused = false, status = 'bot'{"" if keep_order else ", bot_state = '{}'::jsonb"}
             where id = $1 and store_id = $2
            """,
            conversation_id, store_id,
        )
    return await _detail(conn, store_id, conversation_id)


@router.post("/{conversation_id}/reply", response_model=ConversationDetail)
async def owner_reply(
    conversation_id: UUID,
    body: ReplyIn,
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> ConversationDetail:
    async with conn.transaction():
        row = await _locked(conn, store_id, conversation_id)
        adapter = adapter_for(row["channel"])
        if adapter is None:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="هالقناة لسا مش مربوطة للرد من الداشبورد.")
        await db.insert_message(conn, store_id, conversation_id, "owner", body.text, {})
        # The owner is handling it now: the bot stays quiet until "resume".
        await conn.execute(
            "update conversations set status = 'owner' where id = $1 and store_id = $2 and status in ('bot', 'waiting_owner')",
            conversation_id, store_id,
        )
        await db.touch_conversation(conn, store_id, conversation_id)
    try:
        await adapter.send(row["external_id"], OutboundMessage(text=body.text))
    except Exception:
        # Stored and visible in the history; delivery problems are logged without the text.
        log.exception("owner reply not delivered (channel=%s, conversation=%s)", row["channel"], conversation_id)
    return await _detail(conn, store_id, conversation_id)
