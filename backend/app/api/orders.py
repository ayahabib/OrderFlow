"""Orders page: list with filters/search, and status changes (TASKS.md 1.10)."""

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict

from app.api.auth import get_current_store_id
from app.db import orders as db
from app.db.pool import get_db
from app.engine.order_flow import BotState
from app.services import orders as service
from app.services.excel_import import DEFAULT_VARIANT

router = APIRouter(prefix="/api/orders", tags=["orders"])

OrderStatus = Literal["new", "in_progress", "shipped", "delivered", "cancelled", "returned"]
STATUS_LABEL = {"new": "جديد", "in_progress": "قيد التجهيز", "shipped": "تم الشحن", "delivered": "تم التوصيل",
                "cancelled": "ملغي", "returned": "مرتجع"}


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
    duplicate_of_number: int | None = None  # same items as this earlier order, within 24 h (3.5)
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
    order = ["in_progress", "shipped", "delivered", "returned", "cancelled"]
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
    request: Request,
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> OrderOut:
    """Forward only, plus cancel (before delivery) and return (after). Both put the items back in stock."""
    before = await conn.fetchval("select status::text from orders where id = $1 and store_id = $2", order_id, store_id)
    try:
        found = await service.change_status(conn, store_id, order_id, body.status)
    except service.StatusChangeError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"ما بينفع تغيّر طلب «{STATUS_LABEL[exc.current]}» لـ «{STATUS_LABEL[exc.wanted]}»",
        )
    if not found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="الطلب غير موجود")
    request.state.audit = {"before": {"status": before}, "after": {"status": body.status}}   # 3.14
    return _out(await db.get_order(conn, store_id, order_id))


# --- incomplete orders: drafts the customer left (3.5) ---

STALLED_HOURS = 2
STALLED_MAX_DAYS = 7


class StalledDraftOut(BaseModel):
    conversation_id: UUID
    channel: str
    customer_name: str | None
    customer_phone: str | None
    last_message_at: datetime
    items: list[str]          # "فستان صيفي (أحمر / M) × 2"
    missing: list[str]        # what stops it from being an order, in the owner's words


def _missing(state: BotState) -> list[str]:
    missing = []
    if state.queue or (state.pending and state.pending.kind in ("variant", "product")):
        missing.append("تحديد منتج أو خيار")
    if state.pending and state.pending.kind == "stock":
        missing.append("رد على نقص المخزون")
    if not (state.address and state.zone_id):
        missing.append("العنوان")
    if not state.phone:
        missing.append("رقم الهاتف")
    if not missing:
        missing.append("تأكيد العميل")   # everything is there: the customer never pressed confirm
    return missing


@router.get("/incomplete", response_model=list[StalledDraftOut])
async def incomplete_orders(
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> list[StalledDraftOut]:
    """Orders the customer started and left for over STALLED_HOURS hours (last STALLED_MAX_DAYS days)."""
    out = []
    for r in await db.stalled_drafts(conn, store_id, idle_hours=STALLED_HOURS, max_days=STALLED_MAX_DAYS):
        raw = r["bot_state"]
        state = BotState.load(json.loads(raw) if isinstance(raw, str) else raw)
        items = [f"{i.product_name}{'' if i.variant_name == DEFAULT_VARIANT else f' ({i.variant_name})'} × {i.quantity}"
                 for i in state.items] + [q.product_text for q in state.queue]
        out.append(StalledDraftOut(conversation_id=r["conversation_id"], channel=r["channel"],
                                   customer_name=r["customer_name"], customer_phone=r["customer_phone"],
                                   last_message_at=r["last_message_at"], items=items, missing=_missing(state)))
    return out
