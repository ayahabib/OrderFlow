"""Orders page: list with filters/search, and status changes (TASKS.md 1.10)."""

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict

from app.api.auth import get_current_store_id
from app.db import orders as db
from app.db.pool import get_db
from app.services import orders as service

router = APIRouter(prefix="/api/orders", tags=["orders"])

OrderStatus = Literal["new", "in_progress", "delivered", "cancelled"]
STATUS_LABEL = {"new": "جديد", "in_progress": "قيد التنفيذ", "delivered": "تم التوصيل", "cancelled": "ملغي"}


class OrderItemOut(BaseModel):
    product_name: str
    variant_name: str | None
    quantity: int
    unit_price: Decimal
    list_price: Decimal | None
    notes: str | None


class OrderOut(BaseModel):
    id: UUID
    order_number: int
    status: OrderStatus
    customer_name: str | None
    customer_phone: str | None
    channel: str
    delivery_address: str | None
    zone_name: str | None
    delivery_fee: Decimal
    subtotal: Decimal
    total: Decimal
    notes: str | None
    items: list[OrderItemOut]
    allowed_next: list[OrderStatus]  # what the page may offer as buttons
    created_at: datetime
    updated_at: datetime


class StatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: OrderStatus


def _out(row: asyncpg.Record) -> OrderOut:
    data = dict(row)
    if isinstance(data["items"], str):
        data["items"] = json.loads(data["items"])
    order = ["in_progress", "delivered", "cancelled"]
    data["allowed_next"] = [s for s in order if s in service.ALLOWED[data["status"]]]
    return OrderOut(**data)


@router.get("", response_model=list[OrderOut])
async def list_orders(
    status_filter: Annotated[OrderStatus | None, Query(alias="status")] = None,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> list[OrderOut]:
    rows = await db.list_orders(conn, store_id, status=status_filter, date_from=date_from, date_to=date_to,
                                q=q, limit=limit, offset=offset)
    return [_out(r) for r in rows]


@router.patch("/{order_id}/status", response_model=OrderOut)
async def update_status(
    order_id: UUID,
    body: StatusUpdate,
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> OrderOut:
    """Forward only, plus cancel. Cancelling puts the items back in stock."""
    try:
        found = await service.change_status(conn, store_id, order_id, body.status)
    except service.StatusChangeError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"ما بينفع تغيّر طلب «{STATUS_LABEL[exc.current]}» لـ «{STATUS_LABEL[exc.wanted]}»",
        )
    if not found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="الطلب غير موجود")
    return _out(await db.get_order(conn, store_id, order_id))
