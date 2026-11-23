"""Sales dashboard data (task 3.1, ARCHITECTURE.md section 9: GET /api/reports/sales).

All numbers come from SQL; money stays numeric/Decimal (CLAUDE.md conventions). Days are the
store's local days. Cancelled orders don't count. Every query filters by store_id.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.auth import get_current_store_id
from app.db.pool import get_db
from app.services import forecasting

router = APIRouter(prefix="/api/reports", tags=["reports"])

TOP_PRODUCTS = 5


class DayPoint(BaseModel):
    day: date
    orders_count: int
    revenue: Decimal


class TopProduct(BaseModel):
    product_name: str
    quantity: int
    revenue: Decimal


class SalesReport(BaseModel):
    currency: str
    start: date
    end: date
    orders_count: int
    revenue: Decimal
    average_order: Decimal | None   # None when there were no orders
    new_orders: int                 # confirmed by the customer, not started yet (any date)
    in_progress_orders: int         # being prepared / on the way (any date)
    days: list[DayPoint]            # every day of the period, zero when nothing was sold
    top_products: list[TopProduct]


@router.get("/sales", response_model=SalesReport)
async def sales_report(
    days: Annotated[int, Query(ge=7, le=365)] = 30,
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> SalesReport:
    # "Today" in the store's timezone, from Postgres (it has the tz database; Windows Python doesn't).
    store = await conn.fetchrow(
        "select currency, (now() at time zone timezone)::date as today from stores where id = $1", store_id)
    end = store["today"]
    start = end - timedelta(days=days - 1)

    rows = await conn.fetch(
        "select day, orders_count, revenue from daily_sales where store_id = $1 and day between $2 and $3",
        store_id, start, end,
    )
    by_day = {r["day"]: r for r in rows}
    points = []
    for i in range(days):
        d = start + timedelta(days=i)
        r = by_day.get(d)
        points.append(DayPoint(day=d, orders_count=r["orders_count"] if r else 0,
                               revenue=r["revenue"] if r else Decimal("0.00")))

    top = await conn.fetch(
        """
        select i.product_name, sum(i.quantity)::int as quantity, sum(i.unit_price * i.quantity) as revenue
          from order_items i
          join orders o on o.id = i.order_id and o.store_id = i.store_id
          join stores s on s.id = o.store_id
         where i.store_id = $1 and o.status <> 'cancelled'
           and (o.created_at at time zone s.timezone)::date between $2 and $3
         group by i.product_name
         order by quantity desc, revenue desc, i.product_name
         limit $4
        """,
        store_id, start, end, TOP_PRODUCTS,
    )
    open_orders = await conn.fetchrow(
        """
        select count(*) filter (where status = 'new') as new,
               count(*) filter (where status = 'in_progress') as in_progress
          from orders where store_id = $1
        """,
        store_id,
    )

    orders_count = sum(p.orders_count for p in points)
    revenue = sum((p.revenue for p in points), Decimal("0.00"))
    average = (revenue / orders_count).quantize(Decimal("0.01")) if orders_count else None
    return SalesReport(
        currency=store["currency"], start=start, end=end, orders_count=orders_count, revenue=revenue,
        average_order=average, new_orders=open_orders["new"], in_progress_orders=open_orders["in_progress"],
        days=points,
        top_products=[TopProduct(**dict(r)) for r in top],
    )


# --- stock (3.2) -----------------------------------------------------------------------

class LowStockItem(BaseModel):
    variant_id: UUID
    product_id: UUID
    product_name: str
    variant_name: str
    stock_quantity: int
    low_stock_threshold: int


class ProductStock(BaseModel):
    product_id: UUID
    name: str
    variants_count: int
    total_stock: int


@router.get("/low-stock", response_model=list[LowStockItem])
async def low_stock(
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> list[LowStockItem]:
    """Sold out first, then the closest to running out."""
    rows = await conn.fetch(
        """
        select variant_id, product_id, product_name, variant_name, stock_quantity, low_stock_threshold
          from low_stock_variants
         where store_id = $1
         order by stock_quantity, product_name, variant_name
        """,
        store_id,
    )
    return [LowStockItem(**dict(r)) for r in rows]


@router.get("/stock", response_model=list[ProductStock])
async def stock_by_product(
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> list[ProductStock]:
    rows = await conn.fetch(
        """
        select product_id, name, variants_count::int as variants_count, total_stock::int as total_stock
          from product_stock
         where store_id = $1
         order by total_stock, name
        """,
        store_id,
    )
    return [ProductStock(**dict(r)) for r in rows]


# --- stock run-out forecast (3.4) ---

class ForecastItem(BaseModel):
    variant_id: UUID
    product_id: UUID
    product_name: str
    variant_name: str
    stock_quantity: int
    sold: int
    daily_rate: Decimal
    days_left: int
    run_out_date: date


class ForecastReport(BaseModel):
    window_days: int      # sales history used for the average
    within_days: int
    items: list[ForecastItem]


@router.get("/forecast", response_model=ForecastReport)
async def stock_forecast(
    days: Annotated[int, Query(ge=1, le=90)] = 14,
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> ForecastReport:
    """Variants expected to run out within `days` days at their recent sales rate."""
    items = await forecasting.forecast(conn, store_id, days)
    return ForecastReport(window_days=forecasting.WINDOW_DAYS, within_days=days,
                          items=[ForecastItem(**vars(f)) for f in items])
