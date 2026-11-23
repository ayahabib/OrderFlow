"""Synthetic sales history for the stock forecast demo (task 3.4, PRD risk table).

Usage (from backend/):
    uv run python scripts/seed_sales.py --list                     # stores: id + name
    uv run python scripts/seed_sales.py --store <uuid> [--days 28] [--seed 1]
    uv run python scripts/seed_sales.py --store <uuid> --clear     # remove what this script added

It adds past 'delivered' orders (order_items + inventory_movements with reason 'order', dated in
the past) for the store's active variants, each variant with its own random popularity.
Current stock is NOT changed: the orders are history only, so the forecast has something to
work with. Everything is tied to customers with external_id 'synthetic-…', which --clear uses.
Synthetic data only (CLAUDE.md): no real customers.
"""

import argparse
import asyncio
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import asyncpg
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
PREFIX = "synthetic-"
CUSTOMERS = 8


async def connect() -> asyncpg.Connection:
    load_dotenv(BACKEND_DIR / ".env")
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set in backend/.env")
    try:
        return await asyncpg.connect(url, statement_cache_size=0)
    except Exception as exc:  # never echo the URL (it holds the password)
        sys.exit(f"could not connect to the database ({type(exc).__name__})")


async def list_stores(conn) -> None:
    for r in await conn.fetch("select id, name from stores order by created_at"):
        print(r["id"], r["name"])


async def clear(conn, store_id: UUID) -> None:
    async with conn.transaction():
        orders = await conn.fetch(
            """select o.id from orders o join customers c on c.id = o.customer_id and c.store_id = o.store_id
                where o.store_id = $1 and c.external_id like $2""", store_id, PREFIX + "%")
        ids = [r["id"] for r in orders]
        await conn.execute("delete from inventory_movements where store_id = $1 and order_id = any($2)", store_id, ids)
        await conn.execute("delete from orders where store_id = $1 and id = any($2)", store_id, ids)
        await conn.execute("delete from customers where store_id = $1 and external_id like $2", store_id, PREFIX + "%")
    print(f"removed {len(ids)} synthetic orders")


async def seed(conn, store_id: UUID, days: int, rng: random.Random) -> None:
    variants = await conn.fetch(
        """select v.id, v.product_id, v.name as variant_name, p.name as product_name,
                  coalesce(v.price_override, p.price) as price
             from product_variants v join products p on p.id = v.product_id and p.store_id = v.store_id
            where v.store_id = $1 and v.is_active and p.is_active""", store_id)
    if not variants:
        sys.exit("the store has no active products")

    # Units per day for each variant: most sell slowly, a few sell fast (so some run out soon).
    rate = {v["id"]: rng.choice([0, 0.1, 0.2, 0.3, 0.5, 0.8, 1.2, 2.0]) for v in variants}
    now = datetime.now(timezone.utc)

    async with conn.transaction():
        customers = []
        for i in range(CUSTOMERS):
            customers.append(await conn.fetchval(
                """insert into customers (store_id, channel, external_id, name)
                   values ($1, 'web', $2, $3)
                   on conflict (store_id, channel, external_id) do update set name = excluded.name
                   returning id""", store_id, f"{PREFIX}{i + 1}", f"عميل تجريبي {i + 1}"))

        count = 0
        for day in range(days, 0, -1):
            # One order per variant sale on that day (simple; enough for the averages).
            for v in variants:
                qty = sum(1 for _ in range(3) if rng.random() < rate[v["id"]] / 3)
                if qty == 0:
                    continue
                at = now - timedelta(days=day, hours=rng.randint(0, 10), minutes=rng.randint(0, 59))
                subtotal = Decimal(v["price"]) * qty
                number = await conn.fetchval("select next_order_number($1)", store_id)
                order_id = await conn.fetchval(
                    """insert into orders (store_id, order_number, customer_id, status, subtotal, total,
                                           notes, created_at, updated_at)
                       values ($1, $2, $3, 'delivered', $4, $4, 'بيانات تجريبية', $5, $5) returning id""",
                    store_id, number, rng.choice(customers), subtotal, at)
                await conn.execute(
                    """insert into order_items (store_id, order_id, product_id, variant_id, product_name,
                                                variant_name, unit_price, list_price, quantity)
                       values ($1, $2, $3, $4, $5, $6, $7, $7, $8)""",
                    store_id, order_id, v["product_id"], v["id"], v["product_name"], v["variant_name"], v["price"], qty)
                await conn.execute(
                    """insert into inventory_movements (store_id, variant_id, change, reason, order_id, created_at)
                       values ($1, $2, $3, 'order', $4, $5)""", store_id, v["id"], -qty, order_id, at)
                count += 1
    print(f"added {count} synthetic orders over {days} days for {len(variants)} variants (stock unchanged)")


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--store", type=UUID)
    ap.add_argument("--days", type=int, default=28)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--clear", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    conn = await connect()
    try:
        if args.list:
            await list_stores(conn)
        elif not args.store:
            ap.error("--store is required (see --list)")
        elif args.clear:
            await clear(conn, args.store)
        else:
            if not await conn.fetchval("select exists(select 1 from stores where id = $1)", args.store):
                sys.exit("no store with that id")
            await seed(conn, args.store, max(1, min(args.days, 90)), random.Random(args.seed))
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
