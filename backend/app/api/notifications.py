"""Link the owner's Telegram for escalation alerts (task 1.11). Flow: services/notifications.py.

The store comes from the verified token and the chat id from Telegram itself; neither is
ever read from the request body (CLAUDE.md hard rule 3).
"""

from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.auth import get_current_store_id
from app.db import stores as db
from app.db.pool import get_db
from app.services.notifications import (
    LINK_TTL_SECONDS, NotifyError, TelegramNotifier, find_link, get_notifier, make_link_code,
)

router = APIRouter(prefix="/api/store/telegram", tags=["notifications"])

NOT_CONFIGURED = "إشعارات Telegram مش مفعّلة على السيرفر (TELEGRAM_NOTIFY_BOT_TOKEN فاضي)."
TELEGRAM_DOWN = "ما قدرنا نتواصل مع Telegram هلق. جرّب كمان شوي."
BOT_HAS_WEBHOOK = ("بوت الإشعارات مربوط بـ webhook (مستخدم بمكان تاني)، فما منقدر نقرأ رسالة الربط. "
                   "استخدم بوت جديد خاص بالإشعارات من @BotFather.")


class TelegramStatus(BaseModel):
    available: bool  # the server has a notification bot
    linked: bool


class LinkOut(BaseModel):
    url: str
    expires_in: int


def _require(notifier: TelegramNotifier | None) -> TelegramNotifier:
    if notifier is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=NOT_CONFIGURED)
    return notifier


@router.get("", response_model=TelegramStatus)
async def telegram_status(
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
    notifier: TelegramNotifier | None = Depends(get_notifier),
) -> TelegramStatus:
    chat_id = await db.get_owner_telegram_chat_id(conn, store_id)
    return TelegramStatus(available=notifier is not None, linked=chat_id is not None)


@router.post("/link", response_model=LinkOut)
async def create_link(
    store_id: UUID = Depends(get_current_store_id),
    notifier: TelegramNotifier | None = Depends(get_notifier),
) -> LinkOut:
    bot = _require(notifier)
    try:
        username = await bot.bot_username()
    except NotifyError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=TELEGRAM_DOWN) from None
    code = make_link_code(bot.link_secret, store_id)
    return LinkOut(url=f"https://t.me/{username}?start={code}", expires_in=LINK_TTL_SECONDS)


@router.post("/verify", response_model=TelegramStatus)
async def verify_link(
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
    notifier: TelegramNotifier | None = Depends(get_notifier),
) -> TelegramStatus:
    bot = _require(notifier)
    try:
        payloads = await bot.start_payloads()
    except NotifyError as exc:
        if exc.error_code == 409:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=BOT_HAS_WEBHOOK) from None
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=TELEGRAM_DOWN) from None
    chat_id = find_link(bot.link_secret, store_id, payloads)
    if chat_id is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="ما وصلتنا رسالة الربط. افتح الرابط، اضغط Start بالبوت، وبعدين اضغط تحقق.",
        )
    await db.set_owner_telegram_chat_id(conn, store_id, chat_id)
    store = await db.get_store(conn, store_id)
    try:
        await bot.send(chat_id, f"تم ربط إشعارات متجر «{store['name']}» ✓\nرح توصلك هون المحادثات المحوّلة إلك.")
    except NotifyError:
        pass  # linked anyway; the test button shows whether sending works
    return TelegramStatus(available=True, linked=True)


@router.post("/test", status_code=status.HTTP_204_NO_CONTENT)
async def send_test(
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
    notifier: TelegramNotifier | None = Depends(get_notifier),
) -> None:
    bot = _require(notifier)
    chat_id = await db.get_owner_telegram_chat_id(conn, store_id)
    if chat_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="اربط حساب Telegram أول.")
    try:
        await bot.send(chat_id, "رسالة تجربة: الإشعارات شغّالة ✓")
    except NotifyError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=TELEGRAM_DOWN) from None


@router.delete("", response_model=TelegramStatus)
async def unlink(
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
    notifier: TelegramNotifier | None = Depends(get_notifier),
) -> TelegramStatus:
    await db.set_owner_telegram_chat_id(conn, store_id, None)
    return TelegramStatus(available=notifier is not None, linked=False)
