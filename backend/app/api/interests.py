"""Customers waiting for sold-out variants (TASKS.md 3.12), shown on the stock page."""

from datetime import datetime
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.auth import get_current_store_id
from app.config import Settings, get_settings
from app.db.pool import get_db
from app.services import interests as service

router = APIRouter(prefix="/api/interests", tags=["interests"])


class WaitingVariant(BaseModel):
    variant_id: UUID
    product_name: str
    variant_name: str | None
    stock_quantity: int
    waiting: int
    since: datetime
    customer_names: list[str]


class NotifyOut(BaseModel):
    sent: int
    failed: int    # channel not connected any more; those customers stay on the list


@router.get("", response_model=list[WaitingVariant])
async def list_waiting(
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> list[WaitingVariant]:
    """Back in stock first (ready to notify), then the most waited-for."""
    return [WaitingVariant(**dict(r)) for r in await service.waiting(conn, store_id)]


@router.post("/{variant_id}/notify", response_model=NotifyOut)
async def notify_waiting(
    variant_id: UUID,
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> NotifyOut:
    """The owner's click: send the fixed "it's back" message to everyone waiting for this variant."""
    try:
        result = await service.notify(conn, settings, store_id, variant_id)
    except service.NothingInStock:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="المنتج لسا خلصان. زيد المخزون أول وبعدين بلّغهم.")
    return NotifyOut(sent=result.sent, failed=result.failed)
