"""Web chat simulator webhooks: POST /webhooks/web/{store_id}/...

No owner login here (it plays the customer). Instead:
- the store comes from the path and must exist;
- each browser gets a session token signed with WEB_CHAT_SECRET, which binds it to
  that store and one customer id;
- messages are stored once (client_message_id makes retries idempotent), the
  request returns right away, and the reply is produced in a background task;
- the page is public and each message costs an LLM call, so sessions and messages
  are rate-limited per IP, per session and per store (task 3.8).
"""

import logging
from datetime import datetime
from typing import Annotated
from uuid import UUID

import asyncpg
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.channels import web
from app.channels.base import InboundMessage
from app.config import Settings, get_settings
from app.db import conversations as conv_db
from app.db.pool import get_db, get_db_factory
from app.engine import handler
from app.engine.reply_queue import ReplyQueue, get_reply_queue
from app.llm.client import get_llm
from app.llm.embeddings import get_embedder
from app.services.notifications import get_notifier
from app.utils.rate_limit import RateLimiter, Rule

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


HOUR, DAY = 3600, 86400

# Shown in the simulator when a limit is hit, by rule kind (the key prefix).
LIMIT_MESSAGES = {
    "session": "وصلت لحد الرسائل بهالجلسة. ابدأ محادثة جديدة لتكمّل التجربة.",
    "ip-msg": "بعتت رسائل كتير بوقت قصير. جرّب كمان شوي.",
    "ip-session": "فتحت جلسات كتير بوقت قصير. جرّب كمان شوي.",
    "store-day": "المحاكي وصل لحده اليومي لهالمتجر. جرّب بكرا.",
}


def get_web_limiter(request: Request) -> RateLimiter:
    return request.app.state.web_limiter


def _client_ip(request: Request) -> str:
    # Behind a proxy (deploy), run uvicorn with --proxy-headers so this is the real client address.
    return request.client.host if request.client else "unknown"


def _message_rules(settings: Settings, store_id: UUID, session_id: str, ip: str) -> list[Rule]:
    # Most specific first, so the customer sees the message that fits.
    return [
        Rule(f"session:{store_id}:{session_id}", settings.web_chat_max_messages_per_session, DAY),
        Rule(f"ip-msg:{ip}", settings.web_chat_max_messages_per_ip_hour, HOUR),
        Rule(f"store-day:{store_id}", settings.web_chat_max_messages_per_store_day, DAY),
    ]


def _raise_if_limited(limiter: RateLimiter, rules: list[Rule]) -> None:
    rule = limiter.exceeded(rules)
    if rule is not None:
        kind = rule.key.split(":", 1)[0]
        log.info("web chat limit hit: %s", kind)  # no IP or session id in logs
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail=LIMIT_MESSAGES[kind])


def _session(settings: Settings, store_id: UUID, token: str | None) -> str:
    session_id = web.verify_token(_secret(settings), store_id, token)
    if session_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="جلسة غير صالحة، ابدأ محادثة جديدة")
    return session_id


@router.post("/sessions", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def start_session(
    store_id: UUID,
    request: Request,
    settings: Settings = Depends(get_settings),
    conn: asyncpg.Connection = Depends(get_db),
    limiter: RateLimiter = Depends(get_web_limiter),
) -> SessionOut:
    secret = _secret(settings)
    await _require_store(conn, store_id)
    rules = [Rule(f"ip-session:{_client_ip(request)}", settings.web_chat_max_sessions_per_ip_hour, HOUR)]
    _raise_if_limited(limiter, rules)
    limiter.record(rules)
    session_id = web.new_session_id()
    # The customer row is created with the first message, not here, so empty sessions leave no trace.
    return SessionOut(session_token=web.make_token(secret, store_id, session_id), customer_id=session_id)


async def _respond_in_background(
    db_factory, llm, notifier, dashboard_url: str, store_id: UUID, conversation_id: UUID, to: str, text: str,
    embedder=None,
) -> None:
    try:
        async with db_factory() as conn:
            await handler.respond(conn, store_id, conversation_id, to, text, web.WebAdapter(), llm,
                                  notifier=notifier, dashboard_url=dashboard_url, embedder=embedder)
    except Exception:
        # The customer's message is already stored; a failed reply must not crash anything.
        log.exception("web chat reply failed (conversation=%s)", conversation_id)


@router.post("/messages", response_model=MessageAck)
async def post_message(
    store_id: UUID,
    body: MessageIn,
    background: BackgroundTasks,
    request: Request,
    x_session_token: Annotated[str | None, Header()] = None,
    settings: Settings = Depends(get_settings),
    conn: asyncpg.Connection = Depends(get_db),
    db_factory=Depends(get_db_factory),
    llm=Depends(get_llm),
    notifier=Depends(get_notifier),
    embedder=Depends(get_embedder),
    limiter: RateLimiter = Depends(get_web_limiter),
    queue: ReplyQueue = Depends(get_reply_queue),
) -> MessageAck:
    session_id = _session(settings, store_id, x_session_token)
    await _require_store(conn, store_id)
    # Checked before saving: a refused message is not stored and never reaches the LLM.
    rules = _message_rules(settings, store_id, session_id, _client_ip(request))
    _raise_if_limited(limiter, rules)

    msg = InboundMessage(
        store_id=store_id, channel="web", external_customer_id=session_id,
        customer_name=body.customer_name, text=body.text,
        external_message_id=web.external_message_id(session_id, body.client_message_id),
    )
    result = await handler.ingest(conn, msg)
    if result.duplicate:
        return MessageAck(status="duplicate")  # a retry of a counted message: not counted again
    limiter.record(rules)
    if result.bot_should_reply:
        async def reply(text: str) -> None:
            await _respond_in_background(db_factory, llm, notifier, settings.dashboard_url,
                                         store_id, result.conversation_id, session_id, text, embedder=embedder)

        # Quick follow-up messages are answered together after a short pause (task 3.10).
        ticket = queue.add(result.conversation_id, body.text)
        background.add_task(queue.run, result.conversation_id, ticket, settings.chat_debounce_seconds, reply)
    return MessageAck(status="received")


class ConversationState(BaseModel):
    # "bot" | "waiting_owner" | "owner" | "closed", or "none" before the first message.
    status: str
    bot_replies: bool  # False when paused or handed to the owner: the simulator shows a notice


@router.get("/conversation", response_model=ConversationState)
async def get_conversation_state(
    store_id: UUID,
    x_session_token: Annotated[str | None, Header()] = None,
    settings: Settings = Depends(get_settings),
    conn: asyncpg.Connection = Depends(get_db),
) -> ConversationState:
    """Whether the bot is still answering this session's conversation."""
    session_id = _session(settings, store_id, x_session_token)
    row = await conn.fetchrow(
        """
        select c.status::text as status, c.bot_paused
          from conversations c join customers cu on cu.id = c.customer_id and cu.store_id = c.store_id
         where c.store_id = $1 and cu.channel = 'web' and cu.external_id = $2
         order by c.last_message_at desc
         limit 1
        """,
        store_id, session_id,
    )
    if row is None:
        return ConversationState(status="none", bot_replies=True)
    replies = not row["bot_paused"] and row["status"] not in ("owner", "waiting_owner")
    return ConversationState(status=row["status"], bot_replies=replies)


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
