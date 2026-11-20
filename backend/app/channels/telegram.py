"""Telegram customer channel (task 1.13): the store's own bot, connected from Settings.

- Webhook: POST /webhooks/telegram/{channel_id}. Telegram sends our secret in the
  X-Telegram-Bot-Api-Secret-Token header; the secret is derived per channel from
  TELEGRAM_WEBHOOK_SECRET, so nothing extra is stored and one channel's secret can't
  be used for another (hard rule 12).
- Private chats only. Text messages and inline-button presses become InboundMessages;
  a button press arrives as the button's title, exactly like a customer typing it.
- The bot token lives Fernet-encrypted in store_channels.credentials.
"""

import hashlib
import hmac
from dataclasses import dataclass
from uuid import UUID

from app.channels.base import OutboundMessage
from app.channels.telegram_api import TelegramAPI
from app.config import Settings
from app.utils.crypto import decrypt_json

UNSUPPORTED_REPLY = "حالياً بقدر أفهم الرسائل المكتوبة بس. اكتبلي شو بدك 🙏"
_MAX_TEXT = 4000           # Telegram's limit is 4096
_MAX_CALLBACK_BYTES = 64   # Telegram's callback_data limit


class ChannelNotConfigured(Exception):
    pass


def webhook_secret(settings: Settings, channel_id: UUID) -> str:
    """Per-channel secret token ([0-9a-f], 64 chars: within Telegram's allowed set)."""
    if settings.telegram_webhook_secret is None or not settings.telegram_webhook_secret.get_secret_value():
        raise ChannelNotConfigured("TELEGRAM_WEBHOOK_SECRET is not set")
    key = settings.telegram_webhook_secret.get_secret_value().encode()
    return hmac.new(key, f"telegram-webhook:{channel_id}".encode(), hashlib.sha256).hexdigest()


def webhook_url(settings: Settings, channel_id: UUID) -> str:
    return f"{settings.public_base_url.rstrip('/')}/webhooks/telegram/{channel_id}"


@dataclass
class ParsedUpdate:
    chat_id: str
    customer_name: str | None
    text: str | None              # None: a message we can't read (photo, sticker, voice...)
    external_message_id: str
    callback_query_id: str | None = None


def _name(user: dict) -> str | None:
    name = " ".join(p for p in (user.get("first_name"), user.get("last_name")) if p).strip()
    return name[:80] or user.get("username") or None


def parse_update(update: dict) -> ParsedUpdate | None:
    """A customer message from a private chat, or None for anything we ignore (groups, edits, ...)."""
    callback = update.get("callback_query")
    if callback:
        message = callback.get("message") or {}
        chat = message.get("chat") or {}
        if chat.get("type") != "private" or not callback.get("data"):
            return None
        return ParsedUpdate(
            chat_id=str(chat["id"]), customer_name=_name(callback.get("from") or {}), text=callback["data"],
            external_message_id=f"tg:cb:{callback['id']}", callback_query_id=callback["id"],
        )

    message = update.get("message")
    if not message:
        return None
    chat = message.get("chat") or {}
    if chat.get("type") != "private":
        return None
    text = (message.get("text") or "").strip() or None
    return ParsedUpdate(
        chat_id=str(chat["id"]), customer_name=_name(message.get("from") or {}), text=text[:_MAX_TEXT] if text else None,
        external_message_id=f"tg:{chat['id']}:{message['message_id']}",
    )


def _callback_data(title: str) -> str:
    data = title.encode()[:_MAX_CALLBACK_BYTES]
    return data.decode(errors="ignore")


class TelegramAdapter:
    channel = "telegram"

    def __init__(self, token: str):
        self._api = TelegramAPI(token)

    @classmethod
    def from_credentials(cls, settings: Settings, credentials: str) -> "TelegramAdapter":
        return cls(decrypt_json(settings, credentials)["token"])

    async def send(self, to: str, message: OutboundMessage) -> None:
        payload: dict = {"chat_id": int(to), "text": message.text[:_MAX_TEXT], "disable_web_page_preview": True}
        if message.buttons:
            # One row of inline buttons; tapping one sends its title back as the customer's reply.
            payload["reply_markup"] = {"inline_keyboard": [[
                {"text": b.title, "callback_data": _callback_data(b.title)} for b in message.buttons
            ]]}
        await self._api.call("sendMessage", payload)

    async def answer_button(self, callback_query_id: str) -> None:
        """Stop the button's loading spinner in the customer's app."""
        await self._api.call("answerCallbackQuery", {"callback_query_id": callback_query_id})
