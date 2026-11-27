"""Platform-admin queries (tasks 5.2, 5.3).

These are the ONLY queries that read across stores (CLAUDE.md hard rule 3 exception). They are
called from /api/admin/* only, behind `require_admin`, and never return customer messages,
names or phone numbers: counts, totals and store-level facts only.

Demo data is left out unless `include_demo`: stores flagged `is_demo`, and the synthetic customers
of scripts/seed_sales.py (external_id 'synthetic-...').
"""

import json
from decimal import Decimal
from uuid import UUID

import asyncpg

SYNTHETIC_PREFIX = "synthetic-"
# Orders that count as sales (PRD 3.11: cancelled and returned orders are not sales).
SALE = "o.status not in ('cancelled', 'returned')"
PERIODS = {"week": "1 week", "month": "1 month"}
GROWTH_BUCKETS = 12


async def list_stores(conn: asyncpg.Connection) -> list[asyncpg.Record]:
    """Every store with its status and activity. Newest first."""
    return await conn.fetch(
        f"""
        select s.id, s.name, u.email as owner_email, s.created_at, s.currency,
               s.is_demo, s.suspended_at, s.bot_enabled,
               coalesce((select array_agg(sc.channel::text order by sc.channel::text)
                           from store_channels sc where sc.store_id = s.id and sc.is_active), '{{}}') as channels,
               (select max(m.created_at) from messages m where m.store_id = s.id) as last_activity,
               (select count(*) from orders o where o.store_id = s.id and {SALE}) as orders_count
          from stores s
          left join auth.users u on u.id = s.owner_id
         order by s.created_at desc
        """
    )


async def get_store_flags(conn: asyncpg.Connection, store_id: UUID) -> asyncpg.Record | None:
    return await conn.fetchrow("select id, is_demo, suspended_at from stores where id = $1", store_id)


async def set_suspended(conn: asyncpg.Connection, store_id: UUID, suspended: bool) -> None:
    # Keep the first suspension time when suspending twice.
    await conn.execute(
        "update stores set suspended_at = case when $2 then coalesce(suspended_at, now()) end where id = $1",
        store_id, suspended,
    )


async def set_demo(conn: asyncpg.Connection, store_id: UUID, is_demo: bool) -> None:
    await conn.execute("update stores set is_demo = $2 where id = $1", store_id, is_demo)


# --- platform numbers (5.3) -------------------------------------------------------------------
# $1 = include_demo in every query below.

# Orders that count for the platform: joined with their store and customer for the demo filter.
_ORDERS = f"""
    orders o
    join stores s on s.id = o.store_id
    join customers cu on cu.id = o.customer_id and cu.store_id = o.store_id
   where ($1 or (not s.is_demo and cu.external_id not like '{SYNTHETIC_PREFIX}%'))
"""

_CONVERSATIONS = f"""
    conversations c
    join stores s on s.id = c.store_id
    join customers cu on cu.id = c.customer_id and cu.store_id = c.store_id
   where ($1 or (not s.is_demo and cu.external_id not like '{SYNTHETIC_PREFIX}%'))
"""

# An order the bot took start to finish: from a chat with no hand-over and no owner message.
_BY_BOT = """
    o.conversation_id is not null
    and not exists (select 1 from escalations e where e.store_id = o.store_id and e.conversation_id = o.conversation_id)
    and not exists (select 1 from messages m where m.store_id = o.store_id and m.conversation_id = o.conversation_id
                                               and m.sender = 'owner')
"""


async def store_counts(conn: asyncpg.Connection, include_demo: bool) -> asyncpg.Record:
    return await conn.fetchrow(
        f"""
        select count(*) as total,
               count(*) filter (where s.created_at >= date_trunc('month', now())) as new_this_month,
               count(*) filter (where exists (
                   select 1 from messages m
                     join conversations c on c.id = m.conversation_id and c.store_id = m.store_id
                     join customers cu on cu.id = c.customer_id and cu.store_id = c.store_id
                    where m.store_id = s.id and m.created_at >= now() - interval '30 days'
                      and ($1 or cu.external_id not like '{SYNTHETIC_PREFIX}%'))
                 or exists (
                   select 1 from orders o
                     join customers cu on cu.id = o.customer_id and cu.store_id = o.store_id
                    where o.store_id = s.id and o.created_at >= now() - interval '30 days'
                      and ($1 or cu.external_id not like '{SYNTHETIC_PREFIX}%'))) as active_30d
          from stores s
         where ($1 or not s.is_demo)
        """,
        include_demo,
    )


async def sales_by_currency(conn: asyncpg.Connection, include_demo: bool) -> list[asyncpg.Record]:
    """Sales per currency: money is never added up across currencies."""
    return await conn.fetch(
        f"""
        select s.currency, count(*) as orders, sum(o.total) as revenue,
               count(*) filter (where {_BY_BOT}) as orders_by_bot
          from {_ORDERS} and {SALE}
         group by s.currency
         order by count(*) desc, s.currency
        """,
        include_demo,
    )


async def conversation_counts(conn: asyncpg.Connection, include_demo: bool) -> asyncpg.Record:
    return await conn.fetchrow(
        f"""
        select count(*) as total,
               count(*) filter (where exists (select 1 from escalations e
                                               where e.store_id = c.store_id and e.conversation_id = c.id)) as handed_over
          from {_CONVERSATIONS}
        """,
        include_demo,
    )


async def by_channel(conn: asyncpg.Connection, include_demo: bool) -> list[asyncpg.Record]:
    """Conversations and sales orders per channel."""
    return await conn.fetch(
        f"""
        with conv as (
            select c.channel::text as channel, count(*) as conversations from {_CONVERSATIONS} group by 1
        ), ord as (
            -- A customer belongs to one channel, so the order's channel is the customer's.
            select cu.channel::text as channel, count(*) as orders
              from {_ORDERS} and {SALE}
             group by 1
        )
        select coalesce(conv.channel, ord.channel) as channel,
               coalesce(conv.conversations, 0) as conversations, coalesce(ord.orders, 0) as orders
          from conv full join ord on ord.channel = conv.channel
         order by 3 desc, 2 desc, 1
        """,
        include_demo,
    )


async def top_stores(conn: asyncpg.Connection, include_demo: bool, limit: int = 5) -> list[asyncpg.Record]:
    return await conn.fetch(
        f"""
        select s.id, s.name, count(*) as orders
          from {_ORDERS} and {SALE}
         group by s.id, s.name
         order by count(*) desc, s.name
         limit $2
        """,
        include_demo, limit,
    )


async def growth(conn: asyncpg.Connection, include_demo: bool, period: str) -> list[dict]:
    """The last GROWTH_BUCKETS weeks or months: sales orders, revenue per currency, new stores."""
    step = f"interval '{PERIODS[period]}'"  # literal from the whitelist above, never user text
    rows = await conn.fetch(
        f"""
        with b as (
            select gs as start, gs + {step} as stop
              from generate_series(date_trunc($2, now()) - {step} * {GROWTH_BUCKETS - 1},
                                   date_trunc($2, now()), {step}) gs
        )
        select b.start,
               (select count(*) from {_ORDERS} and {SALE}
                   and o.created_at >= b.start and o.created_at < b.stop) as orders,
               (select coalesce(json_object_agg(x.currency, x.revenue), '{{}}')
                  from (select s.currency, sum(o.total) as revenue from {_ORDERS} and {SALE}
                           and o.created_at >= b.start and o.created_at < b.stop
                         group by s.currency) x) as revenue,
               (select count(*) from stores s where ($1 or not s.is_demo)
                   and s.created_at >= b.start and s.created_at < b.stop) as new_stores
          from b
         order by b.start
        """,
        include_demo, period,
    )
    out = []
    for r in rows:
        revenue = r["revenue"]
        if isinstance(revenue, str):
            revenue = json.loads(revenue, parse_float=Decimal, parse_int=Decimal)
        out.append({"start": r["start"], "orders": r["orders"], "new_stores": r["new_stores"],
                    "revenue": {k: Decimal(str(v)) for k, v in (revenue or {}).items()}})
    return out
