"""Stock run-out forecast, the baseline method (task 3.4, ARCHITECTURE.md section 12).

days left = stock_quantity / average daily sales, per variant.
- Sales = net units sold in the last WINDOW_DAYS days from inventory_movements:
  'order' rows are negative, 'order_cancel' rows give units back.
- The average divides by the days of history the variant really has (at most WINDOW_DAYS,
  at least 1), so a product added a week ago isn't averaged over four weeks.
- Variants with no sales have no forecast; sold-out ones are already on the stock page.
All numbers are computed here and in SQL (hard rule 1). Every query filters by store_id.
"""

import math
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

import asyncpg

WINDOW_DAYS = 28


@dataclass
class Forecast:
    variant_id: UUID
    product_id: UUID
    product_name: str
    variant_name: str
    stock_quantity: int
    sold: int                 # net units sold in the window
    daily_rate: Decimal       # units per day, 2 decimals (for display)
    days_left: int            # whole days until it runs out (0 = today)
    run_out_date: date


def days_left(stock: int, sold: int, history_days: Decimal) -> int | None:
    """Whole days the stock lasts at the current rate; None when nothing sold."""
    if sold <= 0:
        return None
    return math.floor(Decimal(stock) * history_days / Decimal(sold))


async def forecast(conn: asyncpg.Connection, store_id: UUID, within_days: int) -> list[Forecast]:
    """Variants expected to run out within `within_days` days, soonest first."""
    rows = await conn.fetch(
        """
        with sold as (
          select m.variant_id,
                 -sum(m.change)::int as sold,
                 min(m.created_at) filter (where m.reason = 'order') as first_sale
            from inventory_movements m
           where m.store_id = $1
             and m.reason in ('order', 'order_cancel')
             and m.created_at >= now() - make_interval(days => $2)
           group by m.variant_id
        )
        select v.id as variant_id, p.id as product_id, p.name as product_name, v.name as variant_name,
               v.stock_quantity, s.sold,
               greatest(1, least($2::numeric,
                   extract(epoch from now() - least(v.created_at, s.first_sale))::numeric / 86400)) as history_days
          from sold s
          join product_variants v on v.id = s.variant_id and v.store_id = $1
          join products p on p.id = v.product_id and p.store_id = $1
         where v.is_active and p.is_active and s.sold > 0 and v.stock_quantity > 0
        """,
        store_id, WINDOW_DAYS,
    )
    # The store's local "today" comes from Postgres (Windows Python has no tz database).
    today = await conn.fetchval("select (now() at time zone timezone)::date from stores where id = $1", store_id)

    result = []
    for r in rows:
        left = days_left(r["stock_quantity"], r["sold"], r["history_days"])
        if left is None or left > within_days:
            continue
        rate = (Decimal(r["sold"]) / r["history_days"]).quantize(Decimal("0.01"), ROUND_HALF_UP)
        result.append(Forecast(r["variant_id"], r["product_id"], r["product_name"], r["variant_name"],
                               r["stock_quantity"], r["sold"], rate, left, today + timedelta(days=left)))
    result.sort(key=lambda f: (f.days_left, f.product_name, f.variant_name))
    return result
