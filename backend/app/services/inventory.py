"""Inventory writes. Every stock change records an inventory_movements row (hard rule 2)."""

import json
from dataclasses import dataclass
from uuid import UUID

import asyncpg

from app.services.excel_import import ImportPlan
from app.utils.arabic import normalize_arabic


class ImportConflictError(Exception):
    """The import clashes with existing data (e.g. SKU used by another variant). Nothing was saved."""


@dataclass
class ImportResult:
    products_created: int = 0
    products_updated: int = 0
    variants_created: int = 0
    variants_updated: int = 0
    movements: int = 0


async def existing_stock(conn: asyncpg.Connection, store_id: UUID) -> dict[str, dict[str, int]]:
    """{normalized product name: {normalized variant name: stock}} for the import preview."""
    rows = await conn.fetch(
        """
        select p.search_text, v.name as variant_name, v.stock_quantity
          from products p
          left join product_variants v on v.product_id = p.id and v.store_id = p.store_id
         where p.store_id = $1
        """,
        store_id,
    )
    stock: dict[str, dict[str, int]] = {}
    for r in rows:
        variants = stock.setdefault(r["search_text"] or "", {})
        if r["variant_name"] is not None:
            variants[normalize_arabic(r["variant_name"])] = r["stock_quantity"]
    return stock


async def _find_product(conn, store_id: UUID, name: str, search_text: str) -> UUID | None:
    # Prefer an exact name match, then the same name with another spelling.
    return await conn.fetchval(
        """
        select id from products
         where store_id = $1 and (name = $2 or search_text = $3)
         order by (name = $2) desc
         limit 1
        """,
        store_id, name, search_text,
    )


async def apply_import(conn: asyncpg.Connection, store_id: UUID, plan: ImportPlan) -> ImportResult:
    """Write the valid part of an import plan in ONE transaction.

    - Product exists (same normalized name): update price/category/description, merge aliases.
    - Variant exists (same product + variant name): set the quantity to the file's value.
    - Each stock change writes a movement with reason 'upload' and change = new - old.
    - price_override and sku are only overwritten when the file gives a value.
    """
    result = ImportResult()
    try:
        async with conn.transaction():
            for product in plan.products:
                product_id = await _find_product(conn, store_id, product.name, product.search_text)
                if product_id is None:
                    product_id = await conn.fetchval(
                        """
                        insert into products (store_id, name, search_text, aliases, description, category, price)
                        values ($1, $2, $3, $4, $5, $6, $7)
                        returning id
                        """,
                        store_id, product.name, product.search_text, product.aliases,
                        product.description, product.category, product.price,
                    )
                    result.products_created += 1
                else:
                    await conn.execute(
                        """
                        update products
                           set price = $3,
                               search_text = $4,
                               category = coalesce($5, category),
                               description = coalesce($6, description),
                               aliases = array(select distinct a from unnest(aliases || $7::text[]) a),
                               is_active = true,
                               updated_at = now()
                         where id = $1 and store_id = $2
                        """,
                        product_id, store_id, product.price, product.search_text,
                        product.category, product.description, product.aliases,
                    )
                    result.products_updated += 1

                existing = {
                    normalize_arabic(r["name"]): r
                    for r in await conn.fetch(
                        "select id, name, stock_quantity from product_variants where product_id = $1 and store_id = $2",
                        product_id, store_id,
                    )
                }

                for variant in product.variants:
                    current = existing.get(normalize_arabic(variant.name))
                    if current is None:
                        variant_id = await conn.fetchval(
                            """
                            insert into product_variants
                                   (store_id, product_id, name, options, sku, price_override, stock_quantity)
                            values ($1, $2, $3, $4::jsonb, $5, $6, $7)
                            returning id
                            """,
                            store_id, product_id, variant.name, json.dumps(variant.options, ensure_ascii=False),
                            variant.sku, variant.price_override, variant.quantity,
                        )
                        change = variant.quantity
                        result.variants_created += 1
                    else:
                        variant_id = current["id"]
                        await conn.execute(
                            """
                            update product_variants
                               set stock_quantity = $3,
                                   options = $4::jsonb,
                                   sku = coalesce($5, sku),
                                   price_override = coalesce($6, price_override),
                                   is_active = true,
                                   updated_at = now()
                             where id = $1 and store_id = $2
                            """,
                            variant_id, store_id, variant.quantity,
                            json.dumps(variant.options, ensure_ascii=False), variant.sku, variant.price_override,
                        )
                        change = variant.quantity - current["stock_quantity"]
                        result.variants_updated += 1

                    if change != 0:
                        await conn.execute(
                            """
                            insert into inventory_movements (store_id, variant_id, change, reason)
                            values ($1, $2, $3, 'upload')
                            """,
                            store_id, variant_id, change,
                        )
                        result.movements += 1
    except asyncpg.UniqueViolationError as exc:
        if "sku" in (exc.constraint_name or ""):
            raise ImportConflictError("في SKU بالملف مستخدم لمنتج تاني بالمتجر. ما انحفظ ولا اشي.") from exc
        raise ImportConflictError("في تعارض مع بيانات موجودة. ما انحفظ ولا اشي.") from exc
    return result
