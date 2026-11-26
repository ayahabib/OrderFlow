"""Public order tracking (task 3.6, PRD item 18): GET /api/track/{token}, no login.

The token is random and unguessable (migration 0018); it is the only way in. The page may be
forwarded, so it shows the order and its status but never the customer's phone, address or name.
The store comes from the order row itself, not from the request (hard rule 3).
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel

from app.db.pool import get_db

router = APIRouter(prefix="/api/track", tags=["tracking"])

Token = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]

STEPS = ["new", "in_progress", "shipped", "delivered"]   # cancelled / returned are shown on their own


class TrackedItem(BaseModel):
    product_name: str
    variant_name: str | None
    quantity: int


class TrackedOrder(BaseModel):
    store_name: str
    currency: str
    order_number: int
    status: str
    steps: list[str]
    zone_name: str | None
    delivery_fee: Decimal
    total: Decimal
    items: list[TrackedItem]
    created_at: datetime
    updated_at: datetime


@router.get("/{token}", response_model=TrackedOrder)
async def track(token: Token, conn: asyncpg.Connection = Depends(get_db)) -> TrackedOrder:
    order = await conn.fetchrow(
        """
        select o.id, o.store_id, o.order_number, o.status::text as status, o.delivery_fee, o.total,
               o.created_at, o.updated_at, s.name as store_name, s.currency, z.name as zone_name
          from orders o
          join stores s on s.id = o.store_id
          left join delivery_zones z on z.id = o.delivery_zone_id and z.store_id = o.store_id
         where o.tracking_token = $1
        """,
        token,
    )
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ما لقينا هالطلب. تأكد من الرابط.")
    items = await conn.fetch(
        """
        select product_name, nullif(variant_name, 'الافتراضي') as variant_name, quantity
          from order_items where order_id = $1 and store_id = $2
         order by product_name, variant_name
        """,
        order["id"], order["store_id"],
    )
    data = {k: order[k] for k in ("store_name", "currency", "order_number", "status", "zone_name",
                                  "delivery_fee", "total", "created_at", "updated_at")}
    return TrackedOrder(**data, steps=STEPS, items=[TrackedItem(**dict(i)) for i in items])
