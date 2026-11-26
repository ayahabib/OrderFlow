"""Pick the adapter that delivers to a conversation's channel (used for owner replies).

Web needs nothing. Telegram uses the store's own connected bot; WhatsApp the store's connected number (5.1).
"""

from uuid import UUID

import asyncpg

from app.channels.base import ChannelAdapter
from app.channels.telegram import TelegramAdapter
from app.channels.web import WebAdapter
from app.channels.whatsapp import WhatsAppAdapter
from app.config import Settings
from app.db import channels as channels_db
from app.utils.crypto import CryptoError


async def adapter_for(conn: asyncpg.Connection, settings: Settings, store_id: UUID, channel: str) -> ChannelAdapter | None:
    """None when the channel can't send right now (the caller tells the owner instead of pretending)."""
    if channel == "web":
        return WebAdapter()
    if channel == "telegram":
        row = await channels_db.get_store_channel(conn, store_id, "telegram")
        if row is None or not row["is_active"] or not row["credentials"]:
            return None
        try:
            return TelegramAdapter.from_credentials(settings, row["credentials"])
        except (CryptoError, KeyError):
            return None
    if channel == "whatsapp":
        # Note: WhatsApp only delivers free-form messages within 24 h of the customer's last message.
        row = await channels_db.get_store_channel(conn, store_id, "whatsapp")
        if row is None or not row["is_active"] or not row["credentials"]:
            return None
        try:
            return WhatsAppAdapter.from_channel(settings, row["external_id"], row["credentials"])
        except (CryptoError, KeyError):
            return None
    return None
