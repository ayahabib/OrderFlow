"""Order totals for the summary (hard rule 1: every number here comes from code/SQL).

Reads current variant prices, applies offers via services/pricing.py, and the
zone fee (or free delivery). Nothing is taken from the LLM or from bot_state.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import asyncpg

from app.services import pricing


@dataclass(frozen=True)
class Line:
    variant_id: UUID
    product_id: UUID
    product_name: str
    variant_name: str
    quantity: int
    list_price: Decimal
    unit_price: Decimal
    offer_name: str | None
    offer_id: UUID | None
    line_total: Decimal


@dataclass(frozen=True)
class Totals:
    lines: list[Line]
    subtotal: Decimal
    delivery_fee: Decimal
    free_delivery_offer: str | None
    free_delivery_offer_id: UUID | None
    total: Decimal


class VariantGoneError(Exception):
    """A variant in the draft was deleted or deactivated since it was matched."""


async def compute_totals(
    conn: asyncpg.Connection, store_id: UUID, items: list[tuple[UUID, int]], zone_fee: Decimal,
    now: datetime | None = None,
) -> Totals:
    """items: [(variant_id, quantity)]."""
    now = now or datetime.now(timezone.utc)
    rows = await conn.fetch(
        """
        select v.id, v.product_id, p.name as product_name, v.name as variant_name,
               coalesce(v.price_override, p.price) as base_price
          from product_variants v
          join products p on p.id = v.product_id and p.store_id = v.store_id
         where v.store_id = $1 and v.id = any($2::uuid[]) and v.is_active and p.is_active
        """,
        store_id, [vid for vid, _ in items],
    )
    by_id = {r["id"]: r for r in rows}
    offers = await pricing.load_offers(conn, store_id)

    lines = []
    for variant_id, quantity in items:
        row = by_id.get(variant_id)
        if row is None:
            raise VariantGoneError(str(variant_id))
        price = pricing.variant_price(row["base_price"], row["product_id"], variant_id, offers, now)
        lines.append(Line(
            variant_id=variant_id, product_id=row["product_id"], product_name=row["product_name"],
            variant_name=row["variant_name"],
            quantity=quantity, list_price=price.list_price, unit_price=price.unit_price,
            offer_name=price.offer.name if price.offer else None,
            offer_id=price.offer.id if price.offer else None,
            line_total=pricing.money(price.unit_price * quantity),
        ))

    subtotal = pricing.money(sum((line.line_total for line in lines), Decimal("0")))
    delivery = pricing.delivery_fee(subtotal, zone_fee, offers, now)
    return Totals(
        lines=lines, subtotal=subtotal, delivery_fee=delivery.fee,
        free_delivery_offer=delivery.offer.name if delivery.offer else None,
        free_delivery_offer_id=delivery.offer.id if delivery.offer else None,
        total=pricing.money(subtotal + delivery.fee),
    )


def is_large_order(totals: Totals, item_limit: int, total_qty_limit: int, amount_limit: Decimal) -> bool:
    """Store limits (stores.large_order_*). Any one reached -> owner handles it (hard rule 5)."""
    return (
        any(line.quantity >= item_limit for line in totals.lines)
        or sum(line.quantity for line in totals.lines) >= total_qty_limit
        or totals.total >= amount_limit
    )
