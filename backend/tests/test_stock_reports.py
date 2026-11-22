"""Stock page data (TASKS.md 3.2): low-stock alerts, stock per product, alert threshold."""

import pytest

from tests.authutil import bearer, client_for, make_token, patch_jwks
from tests.test_orders import make_store

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch):
    patch_jwks(monkeypatch)


async def test_low_stock_alerts_and_threshold(db_tx):
    s = await make_store(db_tx, stock_a=5, stock_b=3)     # default alert level is 5: both are "low"
    h = bearer(make_token(s["owner"]))
    async with client_for(db_tx) as c:
        low = (await c.get("/api/reports/low-stock", headers=h)).json()
        assert [(i["variant_name"], i["stock_quantity"]) for i in low] == [("أسود", 3), ("أبيض", 5)]   # lowest first

        await c.patch(f"/api/variants/{s['a']}", headers=h, json={"stock_quantity": 0})
        low = (await c.get("/api/reports/low-stock", headers=h)).json()
        assert low[0]["variant_name"] == "أبيض" and low[0]["stock_quantity"] == 0   # sold out on top

        r = await c.patch(f"/api/variants/{s['b']}", headers=h, json={"low_stock_threshold": 2})
        assert r.status_code == 200
        low = (await c.get("/api/reports/low-stock", headers=h)).json()
        assert [i["variant_name"] for i in low] == ["أبيض"]                           # 3 > 2: no longer an alert

        bad = await c.patch(f"/api/variants/{s['b']}", headers=h, json={"low_stock_threshold": -1})
        assert bad.status_code == 422

        [product] = (await c.get("/api/reports/stock", headers=h)).json()
        assert (product["name"], product["variants_count"], product["total_stock"]) == ("قميص", 2, 3)

    # Changing the alert level is not a stock movement; the quantity change is.
    reasons = [r["reason"] for r in await db_tx.fetch(
        "select reason::text as reason from inventory_movements where store_id = $1", s["store"])]
    assert reasons == ["manual"]


async def test_stock_reports_are_per_store(db_tx):
    mine = await make_store(db_tx)
    await make_store(db_tx)
    await db_tx.execute("update product_variants set stock_quantity = 100 where store_id = $1", mine["store"])
    async with client_for(db_tx) as c:
        h = bearer(make_token(mine["owner"]))
        assert (await c.get("/api/reports/low-stock", headers=h)).json() == []
        assert [p["total_stock"] for p in (await c.get("/api/reports/stock", headers=h)).json()] == [200]
