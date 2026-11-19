"""services/pricing.py: offers applied in code (TASKS.md 1.3b done-when)."""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.services.pricing import Offer, delivery_fee, money, offer_status, variant_price

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
DRESS, OTHER = uuid.uuid4(), uuid.uuid4()
RED_M, RED_L = uuid.uuid4(), uuid.uuid4()
D = Decimal


def offer(kind="percent_off", **kw) -> Offer:
    return Offer(id=uuid.uuid4(), name="عرض", kind=kind, **kw)


def price(offers, variant=RED_M, product=DRESS, base="120"):
    return variant_price(D(base), product, variant, offers, NOW)


# --- price offers ------------------------------------------------------------

def test_no_offers_keeps_list_price():
    p = price([])
    assert (p.list_price, p.unit_price, p.offer) == (D("120.00"), D("120.00"), None)


def test_percent_off_product():
    o = offer(product_id=DRESS, percent=D("20"))
    p = price([o])
    assert (p.list_price, p.unit_price, p.offer) == (D("120.00"), D("96.00"), o)


def test_fixed_price_product():
    assert price([offer("fixed_price", product_id=DRESS, fixed_price=D("99"))]).unit_price == D("99.00")


def test_fixed_price_above_list_price_is_not_applied():
    p = price([offer("fixed_price", product_id=DRESS, fixed_price=D("150"))])
    assert (p.unit_price, p.offer) == (D("120.00"), None)


def test_variant_offer_only_hits_that_variant():
    o = offer(variant_id=RED_M, percent=D("50"))
    assert price([o], variant=RED_M).unit_price == D("60.00")
    assert price([o], variant=RED_L).unit_price == D("120.00")


def test_offer_on_another_product_is_ignored():
    assert price([offer(product_id=OTHER, percent=D("50"))]).offer is None


def test_several_offers_cheapest_wins_and_they_do_not_stack():
    ten = offer(product_id=DRESS, percent=D("10"))          # 108
    fixed = offer("fixed_price", product_id=DRESS, fixed_price=D("100"))  # 100
    variant = offer(variant_id=RED_M, percent=D("15"))      # 102
    p = price([ten, fixed, variant])
    assert (p.unit_price, p.offer) == (D("100.00"), fixed)  # not 120 * 0.9 * 0.85 ...


def test_rounding_half_up_to_cents():
    assert price([offer(product_id=DRESS, percent=D("33.33"))], base="10").unit_price == D("6.67")
    assert price([offer(product_id=DRESS, percent=D("12.5"))], base="0.20").unit_price == D("0.18")  # 0.175 -> 0.18
    assert money("2.675") == D("2.68")


# --- time window and pause -------------------------------------------------------

def test_inactive_expired_and_future_offers_are_not_applied():
    hour = timedelta(hours=1)
    offers = [
        offer(product_id=DRESS, percent=D("50"), is_active=False),
        offer(product_id=DRESS, percent=D("40"), ends_at=NOW - hour),
        offer(product_id=DRESS, percent=D("30"), starts_at=NOW + hour),
    ]
    assert price(offers).offer is None


def test_window_is_start_inclusive_end_exclusive():
    starts_now = offer(product_id=DRESS, percent=D("10"), starts_at=NOW)
    ends_now = offer(product_id=DRESS, percent=D("20"), ends_at=NOW)
    assert price([starts_now, ends_now]).offer == starts_now


def test_offer_status():
    hour = timedelta(hours=1)
    assert offer_status(offer(product_id=DRESS, percent=D(5)), NOW) == "live"
    assert offer_status(offer(product_id=DRESS, percent=D(5), is_active=False), NOW) == "paused"
    assert offer_status(offer(product_id=DRESS, percent=D(5), starts_at=NOW + hour), NOW) == "scheduled"
    assert offer_status(offer(product_id=DRESS, percent=D(5), ends_at=NOW - hour), NOW) == "ended"


# --- free delivery ---------------------------------------------------------------

def free_over(amount, **kw):
    return offer("free_delivery", min_order_total=D(amount), **kw)


def test_free_delivery_at_exact_threshold():
    o = free_over("200")
    d = delivery_fee(D("200.00"), D("20"), [o], NOW)
    assert (d.fee, d.offer) == (D("0.00"), o)


def test_free_delivery_below_threshold_pays_zone_fee():
    d = delivery_fee(D("199.99"), D("20"), [free_over("200")], NOW)
    assert (d.fee, d.offer) == (D("20.00"), None)


def test_free_delivery_picks_lowest_threshold_and_respects_window():
    low, high = free_over("100"), free_over("150")
    expired = free_over("50", ends_at=NOW - timedelta(days=1))
    d = delivery_fee(D("160"), D("10"), [high, expired, low], NOW)
    assert d.offer == low


def test_price_offers_do_not_affect_delivery_and_vice_versa():
    assert delivery_fee(D("500"), D("10"), [offer(product_id=DRESS, percent=D("50"))], NOW).fee == D("10.00")
    assert price([free_over("0")]).offer is None
