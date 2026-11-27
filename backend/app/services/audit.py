"""Audit log (task 3.14): one row per change, by the owner, the bot or the system.

Owner changes are recorded for every dashboard write by `api.audit.record_owner_change`
(a router dependency), so a new endpoint is covered without remembering to log it.
Bot changes (order placed, conversation handed over, "tell me when it's back") call `log`
directly. Never store secrets or message texts here: only what changed and ids.
"""

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

import asyncpg

Actor = Literal["owner", "bot", "system", "admin"]   # admin: platform admin (5.2)

# Body keys never copied into the log: secrets, and free text that may hold customer data.
SENSITIVE_KEYS = {"token", "bot_token", "access_token", "password", "text", "answer", "content", "credentials", "code"}


def _default(value):
    if isinstance(value, (UUID, Decimal, date, datetime)):
        return str(value)
    raise TypeError(type(value).__name__)


def clean(data: dict | None) -> dict | None:
    """Drop sensitive keys (recursively) so they never reach the log."""
    if data is None:
        return None
    out = {}
    for k, v in data.items():
        if k in SENSITIVE_KEYS:
            continue
        out[k] = clean(v) if isinstance(v, dict) else v
    return out


async def log(conn: asyncpg.Connection, store_id: UUID, actor: Actor, action: str,
              entity_id: UUID | None = None, before: dict | None = None, after: dict | None = None) -> None:
    await conn.execute(
        """
        insert into audit_logs (store_id, actor, action, entity_id, before, after)
        values ($1, $2::audit_actor, $3, $4, $5::jsonb, $6::jsonb)
        """,
        store_id, actor, action, entity_id,
        json.dumps(clean(before), default=_default, ensure_ascii=False) if before is not None else None,
        json.dumps(clean(after), default=_default, ensure_ascii=False) if after is not None else None,
    )
