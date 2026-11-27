"""WhatsApp customer channel (task 5.1, was 2.5) over the official Cloud API (hard rule 13).

- Webhook GET: Meta checks the callback once with hub.verify_token (WHATSAPP_VERIFY_TOKEN).
- Webhook POST: the raw body is signed with the app secret (X-Hub-Signature-256); verified
  before anything else (hard rule 12). The store is found from metadata.phone_number_id
  -> store_channels.external_id, never from anything the customer controls.
- Text messages and reply-button taps become InboundMessages (a tap arrives as the button's
  title, like typing it). Delivery receipts ("statuses") are ignored.
- Confirm/edit/cancel are sent as interactive reply buttons (max 3, titles up to 20 chars).
"""

import hashlib
import hmac
from dataclasses import dataclass

from app.channels.base import OutboundMessage
from app.channels.whatsapp_api import WhatsAppAPI
from app.config import Settings
from app.utils.crypto import decrypt_json

UNSUPPORTED_REPLY = "حالياً بقدر أفهم الرسائل المكتوبة بس. اكتبلي شو بدك 🙏"
_MAX_TEXT = 4000            # WhatsApp allows 4096
_MAX_BUTTONS = 3
_MAX_BUTTON_TITLE = 20
_MAX_INTERACTIVE_BODY = 1024


class ChannelNotConfigured(Exception):
    pass


def verify_signature(settings: Settings, body: bytes, header: str | None) -> bool:
    if settings.whatsapp_app_secret is None or not settings.whatsapp_app_secret.get_secret_value():
        raise ChannelNotConfigured("WHATSAPP_APP_SECRET is not set")
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(settings.whatsapp_app_secret.get_secret_value().encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header.removeprefix("sha256="), expected)


@dataclass
class ParsedMessage:
    phone_number_id: str
    wa_id: str                    # the customer's WhatsApp id (their number)
    customer_name: str | None
    text: str | None              # None: a type we can't read (image, voice, sticker...)
    external_message_id: str
    is_button: bool = False       # a reply-button tap: answered without the pause for split messages


def parse_payload(payload: dict) -> list[ParsedMessage]:
    """All customer messages in one webhook call (Meta may batch several)."""
    out: list[ParsedMessage] = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            phone_number_id = str((value.get("metadata") or {}).get("phone_number_id") or "")
            names = {c.get("wa_id"): ((c.get("profile") or {}).get("name")) for c in value.get("contacts") or []}
            for msg in value.get("messages") or []:
                wa_id, msg_id = msg.get("from"), msg.get("id")
                if not (phone_number_id and wa_id and msg_id):
                    continue
                kind = msg.get("type")
                text = None
                if kind == "text":
                    text = ((msg.get("text") or {}).get("body") or "").strip() or None
                elif kind == "interactive":
                    reply = (msg.get("interactive") or {}).get("button_reply") or {}
                    text = (reply.get("title") or "").strip() or None
                elif kind == "button":   # quick-reply button of a template message
                    text = ((msg.get("button") or {}).get("text") or "").strip() or None
                out.append(ParsedMessage(
                    phone_number_id=phone_number_id, wa_id=str(wa_id), customer_name=(names.get(wa_id) or None),
                    text=text[:_MAX_TEXT] if text else None, external_message_id=f"wa:{msg_id}",
                    is_button=kind in ("interactive", "button"),
                ))
    return out


def message_payload(to: str, message: OutboundMessage) -> dict:
    """Plain text, or interactive reply buttons when the bot offers choices."""
    if not message.buttons:
        return {"to": to, "type": "text", "text": {"body": message.text[:_MAX_TEXT], "preview_url": False}}
    buttons = [
        {"type": "reply", "reply": {"id": b.id[:256], "title": b.title[:_MAX_BUTTON_TITLE]}}
        for b in message.buttons[:_MAX_BUTTONS]
    ]
    return {"to": to, "type": "interactive", "interactive": {
        "type": "button", "body": {"text": message.text[:_MAX_INTERACTIVE_BODY]}, "action": {"buttons": buttons},
    }}


def platform_token(settings: Settings) -> str | None:
    """WHATSAPP_SYSTEM_USER_TOKEN, or None when it isn't set."""
    token = settings.whatsapp_system_user_token
    return token.get_secret_value() if token is not None and token.get_secret_value() else None


class WhatsAppAdapter:
    channel = "whatsapp"

    def __init__(self, phone_number_id: str, access_token: str, version: str):
        self.phone_number_id = phone_number_id
        self._api = WhatsAppAPI(access_token, version)

    @classmethod
    def from_channel(cls, settings: Settings, external_id: str, credentials: str) -> "WhatsAppAdapter":
        """A number connected with its own token (5.1), or by the admin on the platform's WABA (5.4),
        which sends with the platform's System User token. KeyError when neither is available."""
        token = decrypt_json(settings, credentials).get("access_token") or platform_token(settings)
        if not token:
            raise KeyError("access_token")
        return cls(external_id, token, settings.whatsapp_graph_version)

    async def send(self, to: str, message: OutboundMessage) -> None:
        # Long summaries with buttons: send the text first, then the buttons with a short prompt.
        if message.buttons and len(message.text) > _MAX_INTERACTIVE_BODY:
            await self._api.send(self.phone_number_id, message_payload(to, OutboundMessage(text=message.text)))
            message = OutboundMessage(text="اختار:", buttons=message.buttons)
        await self._api.send(self.phone_number_id, message_payload(to, message))
