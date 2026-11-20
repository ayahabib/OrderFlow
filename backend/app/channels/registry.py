"""Pick the adapter that delivers to a conversation's channel.

Web is the only connected channel for now; Telegram comes in task 1.13, WhatsApp in 2.5.
"""

from app.channels.base import ChannelAdapter
from app.channels.web import WebAdapter


def adapter_for(channel: str) -> ChannelAdapter | None:
    """None when the channel can't send yet (the caller tells the owner instead of pretending)."""
    if channel == "web":
        return WebAdapter()
    return None
