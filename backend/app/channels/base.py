"""Channel Adapter contract (ARCHITECTURE.md section 3).

Every channel turns its webhook into an InboundMessage, and knows how to deliver
text/buttons back. The engine never knows which channel a message came from.
Storing messages (customer and bot) is the engine's job for every channel.
"""

from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, Field

ChannelName = Literal["whatsapp", "telegram", "web"]


class Button(BaseModel):
    id: str     # sent back as the reply when tapped, e.g. "confirm"
    title: str  # what the customer sees, e.g. "تأكيد"


class InboundMessage(BaseModel):
    store_id: UUID                 # from the channel config or a verified token, never from the body
    channel: ChannelName
    external_customer_id: str      # WhatsApp number, Telegram chat id, web session id
    customer_name: str | None = None
    text: str
    external_message_id: str       # unique per store, e.g. "web:<session>:<client id>"
    raw: dict = Field(default_factory=dict)  # original payload, for debugging


class OutboundMessage(BaseModel):
    text: str
    buttons: list[Button] = Field(default_factory=list)


class ChannelAdapter(Protocol):
    channel: ChannelName

    async def send(self, to: str, message: OutboundMessage) -> None:
        """Deliver a reply to the customer on this channel."""
        ...
