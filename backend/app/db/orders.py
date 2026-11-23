"""Order queries for the owner's orders page. Every query filters by store_id."""

from datetime import date
from uuid import UUID

import asyncpg

from app.utils.arabic import to_latin_digits

ORDER_SELECT = """
    select o.id, o.order_number, o.status::text as status, o.delivery_address, o.delivery_fee,
           o.subtotal, o.total, o.notes, o.created_at, o.updated_at,
           z.name as zone_name, cu.name as customer_name, cu.phone as customer_phone,
           cu.channel::text as channel,
           (select d.order_number from orders d
             where d.id = o.possible_duplicate_of and d.store_id = o.store_id) as duplicate_of_number,
           coalesce((
             select json_agg(json_build_object(
                      'product_name', i.product_name, 'variant_name', i.variant_name,
                      'quantity', i.quantity, 'unit_price', i.unit_price::text,
                      'list_price', i.list_price::text, 'notes', i.notes)
                    order by i.product_name, i.variant_name)
               from order_items i where i.order_id = o.id and i.store_id = o.store_id
           ), '[]'::json) as items
      from orders o
      join stores s on s.id = o.store_id
      join customers cu on cu.id = o.customer_id and cu.store_id = o.store_id
      left join delivery_zones z on z.id = o.delivery_zone_id and z.store_id = o.store_id
"""


async def list_orders(
    conn: asyncpg.Connection, store_id: UUID, *, status: str | None, date_from: date | None,
    date_to: date | None, q: str | None, limit: int, offset: int,
) -> list[asyncpg.Record]:
    """Newest first. Dates are calendar days in the store's timezone.

    q: "#12" or a short number (up to 4 digits) -> order number only. 5+ digits -> phone.
    Anything else -> customer name, address or product names after Arabic normalization.
    """
    raw = to_latin_digits((q or "").strip())
    digits = raw.lstrip("#")
    number, text = None, raw or None
    if digits.isdigit() and (raw.startswith("#") or len(digits) <= 4):
        number, text = int(digits), None   # order number only: "1" shouldn't match every phone with a 1
    return await conn.fetch(
        ORDER_SELECT + """
         where o.store_id = $1
           and ($2::text is null or o.status::text = $2)
           and ($3::date is null or (o.created_at at time zone s.timezone)::date >= $3)
           and ($4::date is null or (o.created_at at time zone s.timezone)::date <= $4)
           and ($6::int is null or o.order_number = $6)
           and ($5::text is null
                or position(normalize_ar($5) in normalize_ar(
                     coalesce(cu.name, '') || ' ' || coalesce(cu.phone, '') || ' ' ||
                     coalesce(o.delivery_address, '') || ' ' ||
                     coalesce((select string_agg(i.product_name, ' ') from order_items i
                                where i.order_id = o.id and i.store_id = o.store_id), ''))) > 0)
         order by o.created_at desc, o.order_number desc
         limit $7 offset $8
        """,
        store_id, status, date_from, date_to, text, number, limit, offset,
    )


async def get_order(conn: asyncpg.Connection, store_id: UUID, order_id: UUID) -> asyncpg.Record | None:
    return await conn.fetchrow(ORDER_SELECT + " where o.id = $1 and o.store_id = $2", order_id, store_id)


async def stalled_drafts(conn: asyncpg.Connection, store_id: UUID, *, idle_hours: int, max_days: int) -> list[asyncpg.Record]:
    """Conversations where the customer started an order and stopped replying (task 3.5).

    Only chats the bot is still handling: a chat waiting for the owner is already in the inbox,
    and a paused one is the owner's. Quietest-for-longest last is not useful, so newest first.
    """
    return await conn.fetch(
        """
        select c.id as conversation_id, c.channel::text as channel, c.last_message_at, c.bot_state,
               cu.name as customer_name, cu.phone as customer_phone
          from conversations c
          join customers cu on cu.id = c.customer_id and cu.store_id = c.store_id
         where c.store_id = $1 and c.status = 'bot' and not c.bot_paused
           and (jsonb_array_length(coalesce(c.bot_state->'items', '[]')) > 0
                or jsonb_array_length(coalesce(c.bot_state->'queue', '[]')) > 0)
           and c.last_message_at < now() - make_interval(hours => $2)
           and c.last_message_at >= now() - make_interval(days => $3)
         order by c.last_message_at desc
        """,
        store_id, idle_hours, max_days,
    )
