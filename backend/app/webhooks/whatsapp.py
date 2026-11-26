"""WhatsApp Cloud API webhook (task 5.1): one URL for the whole platform (one Meta app).

GET  /webhooks/whatsapp  Meta's one-time check: hub.verify_token must equal WHATSAPP_VERIFY_TOKEN.
POST /webhooks/whatsapp  Signed with the app secret (X-Hub-Signature-256), checked on the raw body
                         before anything else (hard rule 12). The store comes from the number that
                         received the message (metadata.phone_number_id -> store_channels.external_id).
Returns 200 fast; replies run in the background through the same engine and reply queue as the
other channels (hard rule 11: duplicates are dropped by external_message_id).
"""

import hmac
import logging

import asyncpg
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse

from app.channels import whatsapp as wa
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
router = APIRouter(prefix="/webhooks/whatsapp", tags=["webhooks"])

OK = {"ok": True}   # any 2xx: Meta stops retrying


@router.get("", response_class=PlainTextResponse)
async def verify_webhook(
    mode: str | None = Query(None, alias="hub.mode"),
    token: str | None = Query(None, alias="hub.verify_token"),
    challenge: str | None = Query(None, alias="hub.challenge"),
    settings: Settings = Depends(get_settings),
) -> str:
    expected = settings.whatsapp_verify_token.get_secret_value() if settings.whatsapp_verify_token else ""
    if mode == "subscribe" and expected and token and hmac.compare_digest(token, expected) and challenge:
        return challenge
    raise HTTPException(status.HTTP_403_FORBIDDEN, detail="verification failed")


async def _respond_in_background(db_factory, llm, notifier, dashboard_url: str, adapter: wa.WhatsAppAdapter,
                                 store_id, conversation_id, to: str, text: str, embedder=None) -> None:
    try:
        async with db_factory() as conn:
            await handler.respond(conn, store_id, conversation_id, to, text, adapter, llm,
                                  notifier=notifier, dashboard_url=dashboard_url, embedder=embedder)
    except Exception:
        log.exception("whatsapp reply failed (conversation=%s)", conversation_id)


async def _send_quietly(adapter: wa.WhatsAppAdapter, to: str, text: str) -> None:
    try:
        await adapter.send(to, OutboundMessage(text=text))
    except Exception:
        log.warning("whatsapp side message not sent")


@router.post("")
async def whatsapp_webhook(
    request: Request,
    background: BackgroundTasks,
    x_hub_signature_256: str | None = Header(None),
    settings: Settings = Depends(get_settings),
    conn: asyncpg.Connection = Depends(get_db),
    db_factory=Depends(get_db_factory),
    llm=Depends(get_llm),
    notifier=Depends(get_notifier),
    embedder=Depends(get_embedder),
    queue: ReplyQueue = Depends(get_reply_queue),
) -> dict:
    body = await request.body()
    try:
        signed = wa.verify_signature(settings, body, x_hub_signature_256)
    except wa.ChannelNotConfigured:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="whatsapp webhooks are not configured") from None
    if not signed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="invalid signature")

    try:
        payload = await request.json()
    except ValueError:
        return OK
    if not isinstance(payload, dict):
        return OK

    for msg in wa.parse_payload(payload):   # statuses (delivered/read) carry no messages and are skipped
        channel = await channels_db.get_active_by_external_id(conn, "whatsapp", msg.phone_number_id)
        if channel is None:
            continue   # a number no store has connected (any more): drop it
        try:
            adapter = wa.WhatsAppAdapter.from_channel(settings, channel["external_id"], channel["credentials"])
        except (CryptoError, KeyError):
            log.error("whatsapp channel %s credentials unreadable", channel["id"])
            continue
        if msg.text is None:
            background.add_task(_send_quietly, adapter, msg.wa_id, wa.UNSUPPORTED_REPLY)
            continue

        inbound = InboundMessage(
            store_id=channel["store_id"], channel="whatsapp", external_customer_id=msg.wa_id,
            customer_name=msg.customer_name, text=msg.text, external_message_id=msg.external_message_id,
        )
        result = await handler.ingest(conn, inbound)
        if result.bot_should_reply:
            async def reply(text: str, _adapter=adapter, _store=channel["store_id"],
                            _conv=result.conversation_id, _to=msg.wa_id) -> None:
                await _respond_in_background(db_factory, llm, notifier, settings.dashboard_url, _adapter,
                                             _store, _conv, _to, text, embedder=embedder)

            # Typed messages wait a short pause; a reply-button tap is a complete answer (3.10).
            delay = 0 if msg.is_button else settings.chat_debounce_seconds
            ticket = queue.add(result.conversation_id, msg.text)
            background.add_task(queue.run, result.conversation_id, ticket, delay, reply)
    return OK
