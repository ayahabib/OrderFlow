"""Product and variant reads. Every query filters by store_id."""

import json
from uuid import UUID

import asyncpg


def _variant(row: asyncpg.Record) -> dict:
    data = dict(row)
    # asyncpg returns jsonb as text unless a codec is registered.
    if isinstance(data.get("options"), str):
        data["options"] = json.loads(data["options"])
    return data


async def list_products(conn: asyncpg.Connection, store_id: UUID) -> list[dict]:
    """Products with their variants, sorted by name. Effective price is computed in SQL."""
    products = await conn.fetch(
        """
        select id, name, category, price, is_active
          from products
         where store_id = $1
         order by name
        """,
        store_id,
    )
    variants = await conn.fetch(
        """
        select v.id, v.product_id, v.name, v.options, v.sku, v.price_override,
               coalesce(v.price_override, p.price) as effective_price,
               v.stock_quantity, v.low_stock_threshold, v.is_active
          from product_variants v
          join products p on p.id = v.product_id and p.store_id = v.store_id
         where v.store_id = $1
         order by v.name
        """,
        store_id,
    )
    by_product: dict[UUID, list[dict]] = {}
    for v in variants:
        by_product.setdefault(v["product_id"], []).append(_variant(v))
    return [{**dict(p), "variants": by_product.get(p["id"], [])} for p in products]


async def get_variant(conn: asyncpg.Connection, store_id: UUID, variant_id: UUID) -> dict | None:
    row = await conn.fetchrow(
        """
        select v.id, v.product_id, v.name, v.options, v.sku, v.price_override,
               coalesce(v.price_override, p.price) as effective_price,
               v.stock_quantity, v.low_stock_threshold, v.is_active
          from product_variants v
          join products p on p.id = v.product_id and p.store_id = v.store_id
         where v.id = $1 and v.store_id = $2
        """,
        variant_id, store_id,
    )
    return _variant(row) if row else None
