"""Customers page queries (task 3.9). Every query is scoped by store_id.

customers rows are per channel; rows with the same phone_normalized are one person,
so the page groups by coalesce(phone_normalized, id).
"""

from uuid import UUID

import asyncpg

# Orders that count as purchases (same rule as the sales view: not cancelled or returned).
_LIST_SQL = """
with c as (
  select cu.*, coalesce(cu.phone_normalized, cu.id::text) as person
    from customers cu
   where cu.store_id = $1
), o as (
  select customer_id,
         count(*) filter (where status not in ('cancelled', 'returned')) as orders_count,
         coalesce(sum(total) filter (where status not in ('cancelled', 'returned')), 0) as total_spent,
         max(created_at) as last_order_at
    from orders
   where store_id = $1
   group by customer_id
), m as (
  select customer_id, max(last_message_at) as last_message_at
    from conversations
   where store_id = $1
   group by customer_id
)
select (array_agg(c.id order by c.created_at desc))[1] as id,
       (array_agg(c.name order by c.created_at desc) filter (where c.name is not null))[1] as name,
       (array_agg(c.phone order by c.created_at desc) filter (where c.phone is not null))[1] as phone,
       array_agg(distinct c.channel::text) as channels,
       coalesce(sum(o.orders_count), 0)::int as orders_count,
       coalesce(sum(o.total_spent), 0)::numeric(10,2) as total_spent,
       max(o.last_order_at) as last_order_at,
       max(m.last_message_at) as last_message_at
  from c
  left join o on o.customer_id = c.id
  left join m on m.customer_id = c.id
 group by c.person
having $2::text is null
    or bool_or(position(normalize_ar($2) in normalize_ar(coalesce(c.name, '') || ' ' || coalesce(c.phone, ''))) > 0)
    or bool_or(c.phone_normalized like '%' || regexp_replace($2, '[^0-9]', '', 'g') || '%'
               and regexp_replace($2, '[^0-9]', '', 'g') <> '')
 order by greatest(max(o.last_order_at), max(m.last_message_at)) desc nulls last
 limit $3
"""


async def list_customers(conn: asyncpg.Connection, store_id: UUID, q: str | None, limit: int = 200):
    return await conn.fetch(_LIST_SQL, store_id, q or None, limit)


async def person_customer_ids(conn: asyncpg.Connection, store_id: UUID, customer_id: UUID) -> list[UUID]:
    """All customer rows of the same person (same normalized phone) in this store, or [] if unknown."""
    rows = await conn.fetch(
        """
        select cu.id
          from customers cu
          join customers me on me.id = $2 and me.store_id = $1
         where cu.store_id = $1
           and (cu.id = me.id or (me.phone_normalized is not null and cu.phone_normalized = me.phone_normalized))
         order by cu.created_at desc
        """,
        store_id, customer_id,
    )
    return [r["id"] for r in rows]


async def customer_rows(conn: asyncpg.Connection, store_id: UUID, ids: list[UUID]):
    return await conn.fetch(
        """
        select id, channel::text as channel, name, phone, default_address, created_at
          from customers
         where store_id = $1 and id = any($2::uuid[])
         order by created_at desc
        """,
        store_id, ids,
    )


async def customer_orders(conn: asyncpg.Connection, store_id: UUID, ids: list[UUID]):
    return await conn.fetch(
        """
        select o.id, o.order_number, o.status::text as status, o.total, o.created_at,
               (select count(*) from order_items i where i.order_id = o.id and i.store_id = $1)::int as items_count
          from orders o
         where o.store_id = $1 and o.customer_id = any($2::uuid[])
         order by o.created_at desc
        """,
        store_id, ids,
    )


async def customer_conversations(conn: asyncpg.Connection, store_id: UUID, ids: list[UUID]):
    return await conn.fetch(
        """
        select id, channel::text as channel, status::text as status, last_message_at
          from conversations
         where store_id = $1 and customer_id = any($2::uuid[])
         order by last_message_at desc
        """,
        store_id, ids,
    )
