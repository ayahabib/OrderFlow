"""Web chat simulator channel.

A session is a random customer id plus an HMAC token that binds it to one store.
The browser keeps the token; every request proves which store and customer it is
for, so a session can't read or post into another conversation.
Replies are stored in `messages` by the engine; the simulator polls them, so
`send` has nothing to deliver.
"""

import base64
import hashlib
import hmac
import secrets
from uuid import UUID

from app.channels.base import OutboundMessage

_PREFIX = "sim-"


def new_session_id() -> str:
    return _PREFIX + secrets.token_urlsafe(12)


def _signature(secret: str, store_id: UUID, session_id: str) -> str:
    mac = hmac.new(secret.encode(), f"web:{store_id}:{session_id}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def make_token(secret: str, store_id: UUID, session_id: str) -> str:
    return f"{session_id}.{_signature(secret, store_id, session_id)}"


def verify_token(secret: str, store_id: UUID, token: str | None) -> str | None:
    """The session id if the token is valid for this store, else None."""
    if not token or "." not in token:
        return None
    session_id, signature = token.rsplit(".", 1)
    if not session_id.startswith(_PREFIX) or len(session_id) > 64:
        return None
    if not hmac.compare_digest(signature, _signature(secret, store_id, session_id)):
        return None
    return session_id


def external_message_id(session_id: str, client_message_id: str) -> str:
    return f"web:{session_id}:{client_message_id}"


class WebAdapter:
    channel = "web"

    async def send(self, to: str, message: OutboundMessage) -> None:
        # Nothing to push: the engine already stored the reply and the simulator polls for it.
        return None
