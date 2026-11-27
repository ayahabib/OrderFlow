"""Telegram webhook for a store's customer bot (ARCHITECTURE.md section 3, task 1.13).

Order of work (hard rules 11 and 12):
1. Verify the secret token header before touching the database.
2. Find the store from the channel id in the path (never from the payload).
3. Store the message once (duplicates ignored) and return 200 right away.
4. Reply in a background task through the same engine as the web chat.
"""

import hmac
import logging
from typing import Annotated
from uuid import UUID

import asyncpg
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, status

from app.channels import telegram as tg
from app.channels.base import InboundMessage, OutboundMessage
from app.config import Settings, get_settings
from app.db import channels as channels_db
from app.db.pool import get_db, get_db_factory
from app.engine import handler
from app.engine.reply_queue import ReplyQueue, get_reply_queue
from app.llm.client import get_llm
from app.llm.embeddings import get_embedder
from app.services.notifications import get_notifier
from app.utils.crypto import CryptoError

log = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/telegram", tags=["webhooks"])

OK = {"ok": True}  # any 2xx tells Telegram not to retry


async def _respond_in_background(db_factory, llm, notifier, dashboard_url: str, adapter: tg.TelegramAdapter,
                                 store_id: UUID, conversation_id: UUID, to: str, text: str, embedder=None) -> None:
    try:
        async with db_factory() as conn:
            await handler.respond(conn, store_id, conversation_id, to, text, adapter, llm,
                                  notifier=notifier, dashboard_url=dashboard_url, embedder=embedder)
    except Exception:
        log.exception("telegram reply failed (conversation=%s)", conversation_id)


async def _send_quietly(adapter: tg.TelegramAdapter, to: str | None, text: str | None, callback_id: str | None) -> None:
    try:
        if callback_id:
            await adapter.answer_button(callback_id)
        if to and text:
            await adapter.send(to, OutboundMessage(text=text))
    except Exception:
        log.warning("telegram side message not sent")


@router.post("/{channel_id}")
async def telegram_webhook(
    channel_id: UUID,
    request: Request,
    background: BackgroundTasks,
    x_telegram_bot_api_secret_token: Annotated[str | None, Header()] = None,
    settings: Settings = Depends(get_settings),
    conn: asyncpg.Connection = Depends(get_db),
    db_factory=Depends(get_db_factory),
    llm=Depends(get_llm),
    notifier=Depends(get_notifier),
    embedder=Depends(get_embedder),
    queue: ReplyQueue = Depends(get_reply_queue),
) -> dict:
    try:
        expected = tg.webhook_secret(settings, channel_id)
    except tg.ChannelNotConfigured:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="telegram webhooks are not configured") from None
    if not x_telegram_bot_api_secret_token or not hmac.compare_digest(x_telegram_bot_api_secret_token, expected):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="invalid secret token")

    channel = await channels_db.get_active_channel(conn, channel_id, "telegram")
    if channel is None:
        return OK  # disconnected meanwhile: drop the update instead of making Telegram retry
    try:
        adapter = tg.TelegramAdapter.from_credentials(settings, channel["credentials"])
    except (CryptoError, KeyError):
        log.error("telegram channel %s credentials unreadable", channel_id)
        return OK

    try:
        update = await request.json()
    except ValueError:
        return OK
    parsed = tg.parse_update(update) if isinstance(update, dict) else None
    if parsed is None:
        return OK
    if parsed.text is None:
        background.add_task(_send_quietly, adapter, parsed.chat_id, tg.UNSUPPORTED_REPLY, None)
        return OK
    if parsed.callback_query_id:
        background.add_task(_send_quietly, adapter, None, None, parsed.callback_query_id)

    msg = InboundMessage(
        store_id=channel["store_id"], channel="telegram", external_customer_id=parsed.chat_id,
        customer_name=parsed.customer_name, text=parsed.text, external_message_id=parsed.external_message_id,
    )
    result = await handler.ingest(conn, msg)
    await handler.alert_owner_if_store_bot_off(conn, result, msg, notifier, settings.dashboard_url, background)
    if result.bot_should_reply:
        async def reply(text: str) -> None:
            await _respond_in_background(db_factory, llm, notifier, settings.dashboard_url, adapter,
                                         channel["store_id"], result.conversation_id, parsed.chat_id, text,
                                         embedder=embedder)

        # Typed messages wait a short pause so a split-up request is read as one (task 3.10);
        # a button press is a complete answer, so it goes at once.
        delay = 0 if parsed.callback_query_id else settings.chat_debounce_seconds
        ticket = queue.add(result.conversation_id, parsed.text)
        background.add_task(queue.run, result.conversation_id, ticket, delay, reply)
    return OK
