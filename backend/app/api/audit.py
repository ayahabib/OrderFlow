"""Audit log (TASKS.md 3.14): record every owner change, and the page that lists the log.

`record_owner_change` is a router dependency on every router that writes store data. After the
endpoint succeeds (an error skips the code after `yield`), it writes one row: the action is the
endpoint's name, the entity id comes from the path, `after` is the JSON body without sensitive
keys. An endpoint can add exact values by setting `request.state.audit = {"before": ..., "after": ...}`.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from app.api.auth import get_current_store_id
from app.db.pool import get_db
from app.services import audit

router = APIRouter(prefix="/api/audit", tags=["audit"])

WRITE_METHODS = {"POST", "PATCH", "PUT", "DELETE"}
# POST endpoints that only read (search, previews, chat questions) or send a test message.
READ_ONLY_ENDPOINTS = {"search", "preview_import", "chat", "ask", "send_test", "create_link"}


async def record_owner_change(
    request: Request,
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
):
    yield
    route = request.scope.get("route")
    name = getattr(route, "name", None)
    if request.method not in WRITE_METHODS or not name or name in READ_ONLY_ENDPOINTS:
        return
    entity_id = next((v for v in request.path_params.values() if _is_uuid(v)), None)
    explicit = getattr(request.state, "audit", None) or {}
    after = explicit.get("after")
    if after is None and request.headers.get("content-type", "").startswith("application/json"):
        try:
            body = await request.json()
            after = body if isinstance(body, dict) else None
        except ValueError:
            after = None
    await audit.log(conn, store_id, "owner", name, UUID(entity_id) if entity_id else None,
                    before=explicit.get("before"), after=after)


def _is_uuid(value) -> bool:
    try:
        UUID(str(value))
        return True
    except ValueError:
        return False


class AuditEntry(BaseModel):
    id: UUID
    actor: str
    action: str
    entity_id: UUID | None
    before: dict | None
    after: dict | None
    created_at: datetime


@router.get("", response_model=list[AuditEntry])
async def list_audit(
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    actor: Annotated[str | None, Query(pattern="^(owner|bot|system)$")] = None,
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> list[AuditEntry]:
    """Newest first."""
    rows = await conn.fetch(
        """
        select id, actor::text as actor, action, entity_id, before, after, created_at
          from audit_logs
         where store_id = $1 and ($4::text is null or actor::text = $4)
         order by created_at desc
         limit $2 offset $3
        """,
        store_id, limit, offset, actor,
    )
    return [AuditEntry(**{**dict(r), "before": _json(r["before"]), "after": _json(r["after"])}) for r in rows]


def _json(value):
    import json
    return json.loads(value) if isinstance(value, str) else value
