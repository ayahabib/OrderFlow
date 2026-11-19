"""Web chat simulator webhooks: POST /webhooks/web/{store_id}/...

No owner login here (it plays the customer). Instead:
- the store comes from the path and must exist;
- each browser gets a session token signed with WEB_CHAT_SECRET, which binds it to
  that store and one customer id;
- messages are stored once (client_message_id makes retries idempotent), the
  request returns right away, and the reply is produced in a background task.
"""

import logging
from datetime import datetime
from typing import Annotated
from uuid import UUID

import asyncpg
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.channels import web
from app.channels.base import InboundMessage
from app.config import Settings, get_settings
from app.db import conversations as conv_db
from app.db.pool import get_db, get_db_factory
from app.engine import handler

log = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks/web/{store_id}", tags=["web chat"])

MessageText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
ClientId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{8,64}$")]


class SessionOut(BaseModel):
    session_token: str
    customer_id: str


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: MessageText
    client_message_id: ClientId  # random id from the browser; resending it is a no-op
    # Simulator's display name for the customer; saved on the customer row.
    customer_name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)] | None = None


class MessageAck(BaseModel):
    status: str  # "received" | "duplicate"


class ChatMessage(BaseModel):
    id: UUID
    sender: str
    content: str
    buttons: list[dict] = Field(default_factory=list)
    created_at: datetime


def _secret(settings: Settings) -> str:
    if settings.web_chat_secret is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="WEB_CHAT_SECRET is not configured")
    return settings.web_chat_secret.get_secret_value()


async def _require_store(conn, store_id: UUID) -> None:
    if not await conn.fetchval("select 1 from stores where id = $1", store_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="المتجر غير موجود")


def _session(settings: Settings, store_id: UUID, token: str | None) -> str:
    session_id = web.verify_token(_secret(settings), store_id, token)
    if session_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="جلسة غير صالحة، ابدأ محادثة جديدة")
    return session_id


@router.post("/sessions", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def start_session(
    store_id: UUID,
    settings: Settings = Depends(get_settings),
    conn: asyncpg.Connection = Depends(get_db),
) -> SessionOut:
    secret = _secret(settings)
    await _require_store(conn, store_id)
    session_id = web.new_session_id()
    # The customer row is created with the first message, not here, so empty sessions leave no trace.
    return SessionOut(session_token=web.make_token(secret, store_id, session_id), customer_id=session_id)


async def _respond_in_background(db_factory, store_id: UUID, conversation_id: UUID, to: str, text: str) -> None:
    try:
        async with db_factory() as conn:
            await handler.respond(conn, store_id, conversation_id, to, text, web.WebAdapter())
    except Exception:
        # The customer's message is already stored; a failed reply must not crash anything.
        log.exception("web chat reply failed (conversation=%s)", conversation_id)


@router.post("/messages", response_model=MessageAck)
async def post_message(
    store_id: UUID,
    body: MessageIn,
    background: BackgroundTasks,
    x_session_token: Annotated[str | None, Header()] = None,
    settings: Settings = Depends(get_settings),
    conn: asyncpg.Connection = Depends(get_db),
    db_factory=Depends(get_db_factory),
) -> MessageAck:
    session_id = _session(settings, store_id, x_session_token)
    await _require_store(conn, store_id)

    msg = InboundMessage(
        store_id=store_id, channel="web", external_customer_id=session_id,
        customer_name=body.customer_name, text=body.text,
        external_message_id=web.external_message_id(session_id, body.client_message_id),
    )
    result = await handler.ingest(conn, msg)
    if result.duplicate:
        return MessageAck(status="duplicate")
    if result.bot_should_reply:
        background.add_task(_respond_in_background, db_factory, store_id, result.conversation_id, session_id, body.text)
    return MessageAck(status="received")


@router.get("/messages", response_model=list[ChatMessage])
async def get_messages(
    store_id: UUID,
    x_session_token: Annotated[str | None, Header()] = None,
    settings: Settings = Depends(get_settings),
    conn: asyncpg.Connection = Depends(get_db),
) -> list[ChatMessage]:
    """This session's messages only (customer + bot + owner replies)."""
    session_id = _session(settings, store_id, x_session_token)
    customer_id = await conv_db.find_customer(conn, store_id, "web", session_id)
    if customer_id is None:
        return []
    return [
        ChatMessage(id=m["id"], sender=m["sender"], content=m["content"],
                    buttons=m["metadata"].get("buttons", []), created_at=m["created_at"])
        for m in await conv_db.customer_messages(conn, store_id, customer_id)
    ]
