"""Prices after offers (DATABASE.md 3.7). The ONLY place prices are computed (hard rule 1).

Pure functions plus one loader. `now` is always passed in so tests control time.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

import asyncpg

CENT = Decimal("0.01")


def money(value) -> Decimal:
    """Round to 2 places, half up (0.005 -> 0.01)."""
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Offer:
    id: UUID
    name: str
    kind: str  # 'percent_off' | 'fixed_price' | 'free_delivery'
    product_id: UUID | None = None
    variant_id: UUID | None = None
    percent: Decimal | None = None
    fixed_price: Decimal | None = None
    min_order_total: Decimal | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    is_active: bool = True


def is_live(offer: Offer, now: datetime) -> bool:
    """Active and inside [starts_at, ends_at). Missing dates mean open-ended."""
    if not offer.is_active:
        return False
    if offer.starts_at is not None and now < offer.starts_at:
        return False
    if offer.ends_at is not None and now >= offer.ends_at:
        return False
    return True


def offer_status(offer: Offer, now: datetime) -> str:
    """For the UI: 'paused', 'scheduled', 'ended' or 'live'."""
    if not offer.is_active:
        return "paused"
    if offer.starts_at is not None and now < offer.starts_at:
        return "scheduled"
    if offer.ends_at is not None and now >= offer.ends_at:
        return "ended"
    return "live"


def _applies_to(offer: Offer, product_id: UUID, variant_id: UUID) -> bool:
    return offer.variant_id == variant_id or (offer.variant_id is None and offer.product_id == product_id)


def _price_with(offer: Offer, base: Decimal) -> Decimal:
    if offer.kind == "percent_off":
        return money(base * (Decimal(100) - offer.percent) / Decimal(100))
    if offer.kind == "fixed_price":
        return money(min(offer.fixed_price, base))
    raise ValueError(f"{offer.kind} is not a price offer")


@dataclass(frozen=True)
class Price:
    list_price: Decimal        # before offers: coalesce(price_override, product.price)
    unit_price: Decimal        # what the customer pays per item
    offer: Offer | None = None  # the applied offer, if any


def variant_price(base: Decimal, product_id: UUID, variant_id: UUID, offers: list[Offer], now: datetime) -> Price:
    """Cheapest price among live offers on this variant or its product. One offer only, never stacked."""
    base = money(base)
    best = Price(list_price=base, unit_price=base)
    for offer in offers:
        if offer.kind == "free_delivery" or not is_live(offer, now) or not _applies_to(offer, product_id, variant_id):
            continue
        price = _price_with(offer, base)
        if price < best.unit_price:  # an "offer" that isn't cheaper is not applied
            best = Price(list_price=base, unit_price=price, offer=offer)
    return best


@dataclass(frozen=True)
class Delivery:
    fee: Decimal
    offer: Offer | None = None  # free-delivery offer that made it 0


def delivery_fee(subtotal: Decimal, zone_fee: Decimal, offers: list[Offer], now: datetime) -> Delivery:
    """Zone fee, or 0 if a live free-delivery offer's minimum is reached (subtotal is after discounts)."""
    zone_fee = money(zone_fee)
    eligible = [
        o for o in offers
        if o.kind == "free_delivery" and is_live(o, now) and money(subtotal) >= o.min_order_total
    ]
    if zone_fee == 0 or not eligible:
        return Delivery(fee=zone_fee)
    return Delivery(fee=Decimal("0.00"), offer=min(eligible, key=lambda o: o.min_order_total))


async def load_offers(conn: asyncpg.Connection, store_id: UUID) -> list[Offer]:
    """All of the store's offers that are switched on. Time filtering happens in code with `now`."""
    rows = await conn.fetch(
        """
        select id, name, kind::text as kind, product_id, variant_id, percent, fixed_price,
               min_order_total, starts_at, ends_at, is_active
          from offers
         where store_id = $1 and is_active
        """,
        store_id,
    )
    return [Offer(**dict(r)) for r in rows]
