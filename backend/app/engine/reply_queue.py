"""Answer split-up messages together (task 3.10).

Customers often type one request as several quick messages ("بدي" / "فستان أسود" / "مقاس M").
Each message is stored right away (handler.ingest); its text also goes into this per-conversation
buffer. The bot waits a short pause after the *latest* message, then answers everything buffered
in one turn. A message that arrives while a reply is running is answered in the next turn.

In memory, for one server process. The real mutex across processes is the
`for update` row lock in handler.respond; this only groups texts and avoids queuing
several turns for one burst. A restart drops buffered texts (they stay stored in the DB).
"""

import asyncio
from collections.abc import Awaitable, Callable
from uuid import UUID

from fastapi import Request

Reply = Callable[[str], Awaitable[None]]


def get_reply_queue(request: Request) -> "ReplyQueue":
    return request.app.state.reply_queue


class ReplyQueue:
    def __init__(self) -> None:
        self._pending: dict[UUID, list[str]] = {}
        self._ticket: dict[UUID, int] = {}
        self._locks: dict[UUID, asyncio.Lock] = {}

    def add(self, conversation_id: UUID, text: str) -> int:
        """Buffer a text; returns a ticket for run(). Only the latest ticket's run answers."""
        self._pending.setdefault(conversation_id, []).append(text)
        self._ticket[conversation_id] = self._ticket.get(conversation_id, 0) + 1
        return self._ticket[conversation_id]

    async def run(self, conversation_id: UUID, ticket: int, delay: float, reply: Reply) -> None:
        """Background task per message: wait, and answer only if no newer message came meanwhile."""
        if delay > 0:
            await asyncio.sleep(delay)
        if self._ticket.get(conversation_id) != ticket:
            return  # a newer message arrived; its run answers all of them
        lock = self._locks.setdefault(conversation_id, asyncio.Lock())
        async with lock:
            texts = self._pending.pop(conversation_id, [])
            if texts:
                await reply("\n".join(texts))
        # Nothing left to answer: forget the conversation so memory stays small.
        if conversation_id not in self._pending and not lock.locked():
            self._ticket.pop(conversation_id, None)
            self._locks.pop(conversation_id, None)

    def pending_count(self) -> int:
        return len(self._pending)
