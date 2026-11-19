"""Offer queries. Every query filters by store_id."""

from uuid import UUID

import asyncpg

OFFER_COLUMNS = """
    o.id, o.name, o.kind::text as kind, o.product_id, o.variant_id, o.percent, o.fixed_price,
    o.min_order_total, o.starts_at, o.ends_at, o.is_active, o.created_at,
    coalesce(p.name, vp.name) as product_name, v.name as variant_name
"""

OFFER_FROM = """
    from offers o
    left join products p on p.id = o.product_id and p.store_id = o.store_id
    left join product_variants v on v.id = o.variant_id and v.store_id = o.store_id
    left join products vp on vp.id = v.product_id and vp.store_id = o.store_id
"""


async def list_offers(conn: asyncpg.Connection, store_id: UUID) -> list[dict]:
    rows = await conn.fetch(
        f"select {OFFER_COLUMNS} {OFFER_FROM} where o.store_id = $1 order by o.created_at desc", store_id
    )
    return [dict(r) for r in rows]


async def get_offer(conn: asyncpg.Connection, store_id: UUID, offer_id: UUID) -> dict | None:
    row = await conn.fetchrow(
        f"select {OFFER_COLUMNS} {OFFER_FROM} where o.id = $1 and o.store_id = $2", offer_id, store_id
    )
    return dict(row) if row else None


async def target_in_store(conn, store_id: UUID, product_id: UUID | None, variant_id: UUID | None) -> bool:
    if product_id is not None:
        return bool(await conn.fetchval("select 1 from products where id = $1 and store_id = $2", product_id, store_id))
    if variant_id is not None:
        return bool(await conn.fetchval(
            "select 1 from product_variants where id = $1 and store_id = $2", variant_id, store_id
        ))
    return True  # free delivery has no target


async def insert_offer(conn: asyncpg.Connection, store_id: UUID, data: dict) -> UUID:
    return await conn.fetchval(
        """
        insert into offers (store_id, name, kind, product_id, variant_id, percent, fixed_price,
                            min_order_total, starts_at, ends_at, is_active)
        values ($1, $2, $3::offer_kind, $4, $5, $6, $7, $8, $9, $10, $11)
        returning id
        """,
        store_id, data["name"], data["kind"], data.get("product_id"), data.get("variant_id"),
        data.get("percent"), data.get("fixed_price"), data.get("min_order_total"),
        data.get("starts_at"), data.get("ends_at"), data.get("is_active", True),
    )


EDITABLE = ("name", "percent", "fixed_price", "min_order_total", "starts_at", "ends_at", "is_active")


async def update_offer(conn: asyncpg.Connection, store_id: UUID, offer_id: UUID, changes: dict) -> None:
    fields = {k: v for k, v in changes.items() if k in EDITABLE}
    if not fields:
        return
    # Column names come from the fixed EDITABLE whitelist; values are parameters.
    sets = ", ".join(f"{col} = ${i}" for i, col in enumerate(fields, start=3))
    await conn.execute(
        f"update offers set {sets} where id = $1 and store_id = $2", offer_id, store_id, *fields.values()
    )


async def delete_offer(conn: asyncpg.Connection, store_id: UUID, offer_id: UUID) -> bool:
    status = await conn.execute("delete from offers where id = $1 and store_id = $2", offer_id, store_id)
    return status != "DELETE 0"
