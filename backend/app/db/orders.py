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
