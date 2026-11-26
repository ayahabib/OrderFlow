"""Shipped and returned orders (TASKS.md 3.11)."""

import pytest

from tests.authutil import bearer, client_for, make_token, patch_jwks
from tests.test_orders import make_store, order, stock

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch):
    patch_jwks(monkeypatch)


async def move(c, h, order_id, status):
    return await c.patch(f"/api/orders/{order_id}/status", headers=h, json={"status": status})


async def test_return_after_delivery_restocks_once_and_is_final(db_tx):
    s = await make_store(db_tx, stock_a=10, stock_b=10)
    h = bearer(make_token(s["owner"]))
    placed = await order(db_tx, s, [(s["a"], 3), (s["b"], 2)])
    async with client_for(db_tx) as c:
        assert (await move(c, h, placed.order_id, "delivered")).status_code == 200
        r = await move(c, h, placed.order_id, "returned")
        assert (r.status_code, r.json()["status"], r.json()["allowed_next"]) == (200, "returned", [])
        assert (await move(c, h, placed.order_id, "returned")).status_code == 200    # double click: no-op
        for other in ("delivered", "cancelled", "new"):
            assert (await move(c, h, placed.order_id, other)).status_code == 409
    assert (await stock(db_tx, s["a"]), await stock(db_tx, s["b"])) == (10, 10)
    moves = await db_tx.fetch(
        "select change from inventory_movements where order_id = $1 and reason = 'order_return' order by change",
        placed.order_id)
    assert [m["change"] for m in moves] == [2, 3]


async def test_return_only_after_delivery(db_tx):
    s = await make_store(db_tx)
    h = bearer(make_token(s["owner"]))
    placed = await order(db_tx, s, [(s["a"], 1)])
    async with client_for(db_tx) as c:
        for status in ("new", "in_progress", "shipped"):
            if status != "new":
                assert (await move(c, h, placed.order_id, status)).status_code == 200
            r = await move(c, h, placed.order_id, "returned")
            assert r.status_code == 409 and "مرتجع" in r.json()["detail"]


async def test_shipped_order_can_still_be_cancelled_with_stock_back(db_tx):
    s = await make_store(db_tx, stock_a=5)
    h = bearer(make_token(s["owner"]))
    placed = await order(db_tx, s, [(s["a"], 2)])
    async with client_for(db_tx) as c:
        assert (await move(c, h, placed.order_id, "shipped")).status_code == 200
        assert (await move(c, h, placed.order_id, "cancelled")).status_code == 200
    assert await stock(db_tx, s["a"]) == 5


async def test_returned_orders_leave_sales_and_customer_totals(db_tx):
    s = await make_store(db_tx, stock_a=10)
    h = bearer(make_token(s["owner"]))
    kept = await order(db_tx, s, [(s["a"], 1)])        # 50 + 10
    back = await order(db_tx, s, [(s["a"], 2)])        # 100 + 10
    async with client_for(db_tx) as c:
        for o in (kept, back):
            assert (await move(c, h, o.order_id, "delivered")).status_code == 200
        assert (await move(c, h, back.order_id, "returned")).status_code == 200
        report = (await c.get("/api/reports/sales", headers=h, params={"days": 7})).json()
        customers = (await c.get("/api/customers", headers=h)).json()
    assert report["orders_count"] == 1 and report["revenue"] == "60.00"
    assert (customers[0]["orders_count"], customers[0]["total_spent"]) == (1, "60.00")
