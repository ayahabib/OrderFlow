"""Owner notifications on Telegram (ARCHITECTURE.md section 7, PRD 4.6).

One platform bot (TELEGRAM_NOTIFY_BOT_TOKEN) sends every store owner their alerts. It is
not the store's own customer-facing bot (that is a channel, task 1.13).

Linking, without a webhook or a public URL:
1. Settings asks for a link -> https://t.me/<bot>?start=<code>. The code is signed (HMAC)
   and carries the store id and an expiry, so nothing is stored for it.
2. The owner presses Start in Telegram, which sends "/start <code>" to the bot.
3. Settings calls verify: we read the bot's recent updates (getUpdates), find our code and
   save that private chat's id as stores.owner_telegram_chat_id.

A failed alert never breaks or undoes an escalation: the escalation row is already
committed and shows in the dashboard. Failures are logged without message text or the token.
"""

import base64
import hashlib
import hmac
import logging
import time
from typing import Protocol
from uuid import UUID

import asyncpg
import httpx

from app.channels.telegram_api import TelegramAPI, TelegramError
from app.config import Settings, get_settings

log = logging.getLogger(__name__)

LINK_TTL_SECONDS = 3600
_SIG_BYTES = 9

RULE_LABELS = {
    "complaint": "شكوى",
    "discount": "طلب خصم أو سعر خاص",
    "large_order": "طلبية كبيرة",
    "allergy_ingredients": "سؤال عن حساسية أو مكونات",
    "unknown_zone": "ما في مناطق توصيل مسجلة",
    "human_requested": "العميل طلب يحكي مع شخص",
    "not_in_data": "سؤال مش موجود ببيانات المتجر",
    "order_status": "سؤال عن حالة طلب",
    "return_request": "طلب إرجاع أو تبديل",
    "llm_failure": "البوت ما قدر يفهم الرسالة (خطأ تقني)",
}
_SENDER = {"customer": "العميل", "bot": "البوت", "owner": "أنت"}
_HISTORY = 10          # last messages shown in the alert
_MESSAGE_CHARS = 300   # per message
_ALERT_CHARS = 3800    # Telegram's limit is 4096


# Telegram errors carry error_code, e.g. 409 = the bot has a webhook, so getUpdates is refused.
NotifyError = TelegramError


class Notifier(Protocol):
    async def send(self, chat_id: int, text: str) -> None: ...
    async def bot_username(self) -> str: ...
    async def start_payloads(self) -> list[tuple[int, str]]: ...


class TelegramNotifier:
    """The platform notify bot: sendMessage, getMe, getUpdates."""

    def __init__(self, token: str, client: httpx.AsyncClient | None = None):
        self._token = token
        self._api = TelegramAPI(token, client)
        self._username: str | None = None

    @property
    def link_secret(self) -> bytes:
        # Link codes are signed with a key derived from the bot token: no extra secret to manage.
        return hashlib.sha256(b"owner-link:" + self._token.encode()).digest()

    async def _call(self, method: str, payload: dict) -> object:
        return await self._api.call(method, payload)

    async def send(self, chat_id: int, text: str) -> None:
        await self._call("sendMessage", {"chat_id": chat_id, "text": text, "disable_web_page_preview": True})

    async def bot_username(self) -> str:
        if self._username is None:
            self._username = (await self._call("getMe", {}))["username"]
        return self._username

    async def start_payloads(self) -> list[tuple[int, str]]:
        """(chat_id, payload) for recent "/start <payload>" messages in private chats.

        Updates are not acknowledged, so several stores can link at the same time. Telegram
        keeps them for 24 hours (up to 100), which is plenty for a one-time link step.
        """
        updates = await self._call("getUpdates", {"allowed_updates": ["message"], "limit": 100})
        found = []
        for update in updates:
            message = update.get("message") or {}
            chat = message.get("chat") or {}
            parts = (message.get("text") or "").split()
            if chat.get("type") == "private" and len(parts) == 2 and parts[0] == "/start":
                found.append((chat["id"], parts[1]))
        return found


_notifiers: dict[str, TelegramNotifier] = {}


def get_notifier() -> TelegramNotifier | None:
    """FastAPI dependency. None when TELEGRAM_NOTIFY_BOT_TOKEN isn't set (alerts are skipped)."""
    settings: Settings = get_settings()
    if settings.telegram_notify_bot_token is None or not settings.telegram_notify_bot_token.get_secret_value():
        return None
    token = settings.telegram_notify_bot_token.get_secret_value()
    if token not in _notifiers:
        _notifiers[token] = TelegramNotifier(token)
    return _notifiers[token]


# --- link codes -----------------------------------------------------------------------

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def make_link_code(secret: bytes, store_id: UUID, now: float | None = None) -> str:
    """URL-safe code for t.me/<bot>?start=<code> (Telegram allows 64 chars of A-Z a-z 0-9 _ -)."""
    expires = int(now if now is not None else time.time()) + LINK_TTL_SECONDS
    body = store_id.bytes + expires.to_bytes(4, "big")
    return _b64(body + hmac.new(secret, body, hashlib.sha256).digest()[:_SIG_BYTES])


def read_link_code(secret: bytes, code: str, now: float | None = None) -> UUID | None:
    """The store id in a valid, unexpired code; None for anything else."""
    try:
        raw = base64.urlsafe_b64decode(code + "=" * (-len(code) % 4))
    except ValueError:
        return None
    if len(raw) != 20 + _SIG_BYTES:
        return None
    body, sig = raw[:20], raw[20:]
    if not hmac.compare_digest(sig, hmac.new(secret, body, hashlib.sha256).digest()[:_SIG_BYTES]):
        return None
    if int.from_bytes(body[16:], "big") < int(now if now is not None else time.time()):
        return None
    return UUID(bytes=body[:16])


def find_link(secret: bytes, store_id: UUID, payloads: list[tuple[int, str]], now: float | None = None) -> int | None:
    """Chat id of the latest /start message carrying a valid code for this store."""
    for chat_id, code in reversed(payloads):
        if read_link_code(secret, code, now) == store_id:
            return chat_id
    return None


# --- escalation alert -------------------------------------------------------------------

def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_alert(store_name: str, rule: str | None, question: str, history: list[tuple[str, str]], link: str) -> str:
    """Plain-text alert: why, the customer's message, recent history, and a link to the dashboard."""
    head = [f"🔔 محادثة محوّلة إلك - {store_name}",
            f"السبب: {RULE_LABELS.get(rule or '', 'تحويل لصاحب المتجر')}",
            f"رسالة العميل: {_clip(question, _MESSAGE_CHARS)}"]
    tail = ["", f"افتح المحادثة: {link}"]
    lines = [f"{_SENDER.get(sender, sender)}: {_clip(text, _MESSAGE_CHARS)}" for sender, text in history]
    # Drop the oldest messages until the alert fits Telegram's limit.
    while lines:
        text = "\n".join(head + ["", "آخر الرسائل:"] + lines + tail)
        if len(text) <= _ALERT_CHARS:
            return text
        lines.pop(0)
    return "\n".join(head + tail)[:_ALERT_CHARS]


def build_bot_off_alert(store_name: str, text: str, link: str) -> str:
    """Alert for a new customer message while the store's auto-reply is off (task 5.5)."""
    return "\n".join([
        f"💬 رسالة جديدة - {store_name}",
        "الرد الآلي موقوف، فالعميل مستني ردّك.",
        f"رسالة العميل: {_clip(text, _MESSAGE_CHARS)}",
        "",
        f"افتح المحادثة: {link}",
    ])


async def bot_off_alert(
    conn: asyncpg.Connection, store_id: UUID, conversation_id: UUID, text: str, dashboard_url: str,
) -> tuple[int, str] | None:
    """(chat_id, alert text) for the owner, or None when the notify bot isn't linked.

    Built inside the webhook (one cheap query) so the slow Telegram call can run in the background.
    """
    store = await conn.fetchrow("select name, owner_telegram_chat_id from stores where id = $1", store_id)
    if store is None or store["owner_telegram_chat_id"] is None:
        return None
    link = f"{dashboard_url.rstrip('/')}/conversations/{conversation_id}"
    return store["owner_telegram_chat_id"], build_bot_off_alert(store["name"], text, link)


async def send_quietly(notifier: Notifier, chat_id: int, text: str) -> None:
    """Background send: a failed alert is logged and never affects the stored message."""
    try:
        await notifier.send(chat_id, text)
    except Exception:
        log.warning("owner alert not sent (chat alert for a new message)")


async def notify_escalation(
    conn: asyncpg.Connection, notifier: Notifier | None, store_id: UUID, conversation_id: UUID, dashboard_url: str,
) -> bool:
    """Send the owner an alert for the conversation's latest escalation. True if it was sent."""
    if notifier is None:
        return False
    store = await conn.fetchrow("select name, owner_telegram_chat_id from stores where id = $1", store_id)
    if store is None or store["owner_telegram_chat_id"] is None:
        return False  # not linked yet: the escalation still shows in the dashboard
    escalation = await conn.fetchrow(
        """
        select rule, question from escalations
         where store_id = $1 and conversation_id = $2
         order by created_at desc limit 1
        """,
        store_id, conversation_id,
    )
    if escalation is None:
        return False
    rows = await conn.fetch(
        """
        select sender::text as sender, content from messages
         where store_id = $1 and conversation_id = $2
         order by created_at desc limit $3
        """,
        store_id, conversation_id, _HISTORY,
    )
    history = [(r["sender"], r["content"] or "") for r in reversed(rows)]
    link = f"{dashboard_url.rstrip('/')}/conversations/{conversation_id}"
    text = build_alert(store["name"], escalation["rule"], escalation["question"], history, link)
    try:
        await notifier.send(store["owner_telegram_chat_id"], text)
    except NotifyError as exc:
        log.warning("owner alert not sent (store=%s, conversation=%s): %s", store_id, conversation_id, exc)
        return False
    return True
