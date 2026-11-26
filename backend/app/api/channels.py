"""Connect the store's customer channels: its own Telegram bot (task 1.13) and WhatsApp number (task 5.1).

POST checks the token with Telegram (getMe), stores it encrypted, and registers our
webhook with a per-channel secret. The DB change and setWebhook happen in one
transaction: if Telegram refuses, nothing is saved. store_id comes from the token.
"""

import re
from typing import Annotated
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, StringConstraints

from app.api.auth import get_current_store_id
from app.channels import telegram as tg
from app.channels.telegram_api import TelegramAPI, TelegramError
from app.channels.whatsapp_api import WhatsAppAPI, WhatsAppError
from app.config import Settings, get_settings
from app.db import channels as db
from app.db.pool import get_db
from app.utils.crypto import CryptoError, decrypt_json, encrypt_json

router = APIRouter(prefix="/api/channels", tags=["channels"])

TOKEN_RE = re.compile(r"^\d{5,15}:[A-Za-z0-9_-]{30,50}$")
TELEGRAM_DOWN = "ما قدرنا نتواصل مع Telegram هلق. جرّب كمان شوي."


class TelegramChannelStatus(BaseModel):
    connected: bool
    bot_username: str | None = None
    webhook_ok: bool | None = None       # None = not checked (not connected or Telegram unreachable)
    webhook_error: str | None = None     # Telegram's last delivery error, e.g. the tunnel is down


class ConnectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bot_token: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


def _require_config(settings: Settings) -> None:
    if not settings.public_base_url.startswith("https://"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Telegram بده رابط https عام للسيرفر. شغّل ngrok وحط الرابط بـ PUBLIC_BASE_URL وأعد تشغيل السيرفر.",
        )
    try:
        tg.webhook_secret(settings, UUID(int=0))
        encrypt_json(settings, {})
    except (tg.ChannelNotConfigured, CryptoError):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="ربط القنوات مش مفعّل على السيرفر (CHANNEL_ENCRYPTION_KEY أو TELEGRAM_WEBHOOK_SECRET).") from None


@router.get("/telegram", response_model=TelegramChannelStatus)
async def telegram_channel_status(
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TelegramChannelStatus:
    row = await db.get_store_channel(conn, store_id, "telegram")
    if row is None or not row["is_active"] or not row["credentials"]:
        return TelegramChannelStatus(connected=False)
    try:
        creds = decrypt_json(settings, row["credentials"])
    except CryptoError:
        return TelegramChannelStatus(connected=True, webhook_ok=False, webhook_error="ما قدرنا نقرأ التوكن المحفوظ. اربط البوت من جديد.")
    out = TelegramChannelStatus(connected=True, bot_username=creds.get("username"))
    try:
        info = await TelegramAPI(creds["token"]).call("getWebhookInfo")
    except TelegramError:
        return out
    expected = tg.webhook_url(settings, row["id"])
    out.webhook_ok = info.get("url") == expected and not info.get("last_error_message")
    if info.get("url") != expected:
        out.webhook_error = "رابط الـ webhook تغيّر (مثلاً رابط ngrok جديد). اربط البوت من جديد."
    elif info.get("last_error_message"):
        out.webhook_error = f"Telegram ما قدر يوصل للسيرفر: {info['last_error_message'][:150]}"
    return out


@router.post("/telegram", response_model=TelegramChannelStatus)
async def connect_telegram(
    body: ConnectIn,
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TelegramChannelStatus:
    _require_config(settings)
    token = body.bot_token
    if not TOKEN_RE.match(token):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="شكل التوكن مش صحيح. انسخه كامل من @BotFather.")
    notify = settings.telegram_notify_bot_token
    if notify is not None and notify.get_secret_value() == token:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="هاد بوت الإشعارات تبع المنصة. اعمل بوت تاني للعملاء من @BotFather.")

    api = TelegramAPI(token)
    try:
        me = await api.call("getMe")
    except TelegramError as exc:
        if exc.error_code in (401, 404):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="التوكن غلط أو البوت انحذف.") from None
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=TELEGRAM_DOWN) from None

    bot_id = str(me["id"])
    if await db.external_id_taken(conn, "telegram", bot_id, store_id):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="هالبوت مربوط بمتجر تاني.")

    async with conn.transaction():
        credentials = encrypt_json(settings, {"token": token, "username": me.get("username")})
        channel_id = await db.save_channel(conn, store_id, "telegram", bot_id, credentials)
        try:
            await api.call("setWebhook", {
                "url": tg.webhook_url(settings, channel_id),
                "secret_token": tg.webhook_secret(settings, channel_id),
                "allowed_updates": ["message", "callback_query"],
                "drop_pending_updates": True,
            })
        except TelegramError:
            # Raising rolls the transaction back: nothing is saved if Telegram refused.
            raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                                detail="Telegram رفض رابط الـ webhook. تأكد إن ngrok شغّال وإن PUBLIC_BASE_URL صحيح.") from None
    return TelegramChannelStatus(connected=True, bot_username=me.get("username"), webhook_ok=True)


@router.delete("/telegram", response_model=TelegramChannelStatus)
async def disconnect_telegram(
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TelegramChannelStatus:
    row = await db.get_store_channel(conn, store_id, "telegram")
    if row is not None and row["credentials"]:
        try:
            await TelegramAPI(decrypt_json(settings, row["credentials"])["token"]).call("deleteWebhook")
        except (TelegramError, CryptoError, KeyError):
            pass  # disconnect locally anyway; the webhook then just gets "ok" and is dropped
    await db.disable_channel(conn, store_id, "telegram")
    return TelegramChannelStatus(connected=False)


# --- WhatsApp Cloud API (task 5.1) --------------------------------------------------------------
# The platform has one Meta app; its webhook (/webhooks/whatsapp) is set once in the Meta dashboard.
# Each store connects its own number: the Phone Number ID and an access token that can use it.

WHATSAPP_DOWN = "ما قدرنا نتواصل مع واتساب (Meta) هلق. جرّب كمان شوي."


class WhatsAppChannelStatus(BaseModel):
    connected: bool
    display_phone_number: str | None = None
    verified_name: str | None = None
    webhook_url: str                       # paste in Meta: WhatsApp > Configuration > Callback URL
    webhook_ready: bool                    # the server has an https address and the webhook secrets set


class WhatsAppConnectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phone_number_id: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\d{6,25}$")]
    access_token: Annotated[str, StringConstraints(strip_whitespace=True, min_length=20, max_length=1000)]


def _whatsapp_status(settings: Settings, connected: bool, creds: dict | None = None) -> WhatsAppChannelStatus:
    ready = (settings.public_base_url.startswith("https://")
             and bool(settings.whatsapp_app_secret and settings.whatsapp_app_secret.get_secret_value())
             and bool(settings.whatsapp_verify_token and settings.whatsapp_verify_token.get_secret_value()))
    creds = creds or {}
    return WhatsAppChannelStatus(
        connected=connected, display_phone_number=creds.get("display_phone_number"),
        verified_name=creds.get("verified_name"),
        webhook_url=f"{settings.public_base_url.rstrip('/')}/webhooks/whatsapp", webhook_ready=ready,
    )


@router.get("/whatsapp", response_model=WhatsAppChannelStatus)
async def whatsapp_channel_status(
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> WhatsAppChannelStatus:
    row = await db.get_store_channel(conn, store_id, "whatsapp")
    if row is None or not row["is_active"] or not row["credentials"]:
        return _whatsapp_status(settings, False)
    try:
        creds = decrypt_json(settings, row["credentials"])
    except CryptoError:
        return _whatsapp_status(settings, True)
    return _whatsapp_status(settings, True, creds)


@router.post("/whatsapp", response_model=WhatsAppChannelStatus)
async def connect_whatsapp(
    body: WhatsAppConnectIn,
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> WhatsAppChannelStatus:
    try:
        encrypt_json(settings, {})
    except CryptoError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="ربط القنوات مش مفعّل على السيرفر (CHANNEL_ENCRYPTION_KEY).") from None

    # Prove the token can use this number before saving anything.
    try:
        info = await WhatsAppAPI(body.access_token, settings.whatsapp_graph_version).phone_number(body.phone_number_id)
    except WhatsAppError as exc:
        if exc.status in (400, 401, 403, 404) or exc.code in (100, 190):
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                detail="الـ Phone Number ID أو التوكن غلط، أو التوكن ما إله صلاحية على هالرقم.") from None
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=WHATSAPP_DOWN) from None

    if await db.external_id_taken(conn, "whatsapp", body.phone_number_id, store_id):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="هالرقم مربوط بمتجر تاني.")
    creds = {"access_token": body.access_token, "display_phone_number": info.get("display_phone_number"),
             "verified_name": info.get("verified_name")}
    await db.save_channel(conn, store_id, "whatsapp", body.phone_number_id, encrypt_json(settings, creds))
    return _whatsapp_status(settings, True, creds)


@router.delete("/whatsapp", response_model=WhatsAppChannelStatus)
async def disconnect_whatsapp(
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> WhatsAppChannelStatus:
    """Forget the token; messages to this number are then dropped (the Meta webhook stays, it's platform-wide)."""
    await db.disable_channel(conn, store_id, "whatsapp")
    return _whatsapp_status(settings, False)
