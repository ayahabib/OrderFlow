"""Customers page (TASKS.md 3.9): one row per person (same phone across channels), with
their orders, spend and conversations. Read only."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.api.auth import get_current_store_id
from app.db import customers as db
from app.db.pool import get_db

router = APIRouter(prefix="/api/customers", tags=["customers"])


class CustomerSummary(BaseModel):
    id: UUID                    # newest customer row of this person; open the detail with it
    name: str | None
    phone: str | None
    channels: list[str]
    orders_count: int
    total_spent: Decimal        # all orders except cancelled and returned
    last_order_at: datetime | None
    last_message_at: datetime | None


class CustomerOrder(BaseModel):
    id: UUID
    order_number: int
    status: str
    total: Decimal
    items_count: int
    created_at: datetime


class CustomerConversation(BaseModel):
    id: UUID
    channel: str
    status: str
    last_message_at: datetime


class CustomerDetail(CustomerSummary):
    address: str | None
    first_seen_at: datetime
    orders: list[CustomerOrder]
    conversations: list[CustomerConversation]


@router.get("", response_model=list[CustomerSummary])
async def list_customers(
    q: Annotated[str | None, Query(max_length=100)] = None,
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> list[CustomerSummary]:
    """Most recently active first. q matches name or phone (any format)."""
    rows = await db.list_customers(conn, store_id, (q or "").strip() or None)
    return [CustomerSummary(**dict(r)) for r in rows]


@router.get("/{customer_id}", response_model=CustomerDetail)
async def get_customer(
    customer_id: UUID,
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> CustomerDetail:
    ids = await db.person_customer_ids(conn, store_id, customer_id)
    if not ids:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="العميل غير موجود")
    rows = await db.customer_rows(conn, store_id, ids)
    orders = await db.customer_orders(conn, store_id, ids)
    convs = await db.customer_conversations(conn, store_id, ids)

    def newest(field):
        return next((r[field] for r in rows if r[field]), None)

    counted = [o for o in orders if o["status"] not in ("cancelled", "returned")]
    return CustomerDetail(
        id=rows[0]["id"], name=newest("name"), phone=newest("phone"),
        channels=sorted({r["channel"] for r in rows}),
        orders_count=len(counted), total_spent=sum((o["total"] for o in counted), Decimal("0.00")),
        last_order_at=orders[0]["created_at"] if orders else None,
        last_message_at=convs[0]["last_message_at"] if convs else None,
        address=newest("default_address"),
        # An order can be older than its customer row (imported/seeded data), so take the earliest of both.
        first_seen_at=min([r["created_at"] for r in rows] + [o["created_at"] for o in orders]),
        orders=[CustomerOrder(**dict(o)) for o in orders],
        conversations=[CustomerConversation(**dict(c)) for c in convs],
    )
