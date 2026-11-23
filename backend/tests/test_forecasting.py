"""Stock run-out forecast (TASKS.md 3.4): days left = stock / average daily sales."""

from decimal import Decimal

import pytest

from app.services.forecasting import days_left
from tests.authutil import bearer, client_for, make_token, patch_jwks
from tests.test_orders import make_store


def test_days_left_formula():
    assert days_left(10, 3, Decimal(10)) == 33          # 0.3 a day -> 33.3 days, floored
    assert days_left(3, 6, Decimal(2)) == 1             # 3 a day
    assert days_left(2, 6, Decimal(2)) == 0             # runs out today
    assert days_left(5, 0, Decimal(28)) is None         # nothing sold: no forecast
    assert days_left(5, -1, Decimal(28)) is None        # more cancelled than sold


@pytest.fixture
def fake_jwks(monkeypatch):
    patch_jwks(monkeypatch)


async def sale(conn, s, variant, units, days_ago, reason="order"):
    await conn.execute(
        """insert into inventory_movements (store_id, variant_id, change, reason, created_at)
           values ($1, $2, $3, $4::movement_reason, now() - make_interval(days => $5))""",
        s["store"], variant, -units if reason == "order" else units, reason, days_ago)


@pytest.mark.db
async def test_forecast_lists_variants_running_out_soonest_first(db_tx, fake_jwks):
    s = await make_store(db_tx, stock_a=10, stock_b=3)
    await sale(db_tx, s, s["a"], 2, 10)
    await sale(db_tx, s, s["a"], 2, 5)
    await sale(db_tx, s, s["a"], 1, 5, reason="order_cancel")   # net 3 sold over 10 days of history
    await sale(db_tx, s, s["a"], 50, 40)                        # outside the 28-day window: ignored
    await sale(db_tx, s, s["b"], 6, 2)                          # 3 a day, 3 left -> 1 day

    h = bearer(make_token(s["owner"]))
    async with client_for(db_tx) as c:
        soon = (await c.get("/api/reports/forecast?days=14", headers=h)).json()
        assert soon["window_days"] == 28
        assert [(i["variant_name"], i["days_left"], i["sold"]) for i in soon["items"]] == [("أسود", 1, 6)]
        assert Decimal(soon["items"][0]["daily_rate"]) == Decimal("3.00")

        later = (await c.get("/api/reports/forecast?days=40", headers=h)).json()
        assert [(i["variant_name"], i["days_left"]) for i in later["items"]] == [("أسود", 1), ("أبيض", 33)]
        assert Decimal(later["items"][1]["daily_rate"]) == Decimal("0.30")

        assert (await c.get("/api/reports/forecast?days=0", headers=h)).status_code == 422


@pytest.mark.db
async def test_forecast_skips_sold_out_and_is_per_store(db_tx, fake_jwks):
    mine = await make_store(db_tx, stock_a=0, stock_b=3)
    other = await make_store(db_tx)
    await sale(db_tx, mine, mine["a"], 5, 3)        # sold out already: on the stock page, not here
    await sale(db_tx, other, other["b"], 9, 3)      # another store's sales
    async with client_for(db_tx) as c:
        r = (await c.get("/api/reports/forecast?days=30", headers=bearer(make_token(mine["owner"])))).json()
    assert r["items"] == []
