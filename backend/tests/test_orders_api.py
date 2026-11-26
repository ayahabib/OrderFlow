"""Orders page API (TASKS.md 1.10): list, filter, search, status changes, cancel returns stock."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from tests.authutil import bearer, client_for, make_token, patch_jwks
from tests.test_orders import make_store, order, stock

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch):
    patch_jwks(monkeypatch)


async def setup(conn):
    s = await make_store(conn, stock_a=10, stock_b=10)
    await conn.execute("update customers set name = 'سارة أحمد' where id = $1", s["customer"])
    return s, bearer(make_token(s["owner"]))


async def test_list_shows_order_with_items_customer_and_totals(db_tx):
    s, h = await setup(db_tx)
    placed = await order(db_tx, s, [(s["a"], 2), (s["b"], 1)])
    async with client_for(db_tx) as c:
        r = await c.get("/api/orders", headers=h)
    assert r.status_code == 200
    [o] = r.json()
    assert (o["order_number"], o["status"], o["total"], o["subtotal"], o["delivery_fee"]) == (1, "new", "140.00", "130.00", "10.00")
    assert (o["customer_name"], o["customer_phone"], o["zone_name"]) == ("سارة أحمد", "0599123456", "نابلس")
    assert o["delivery_address"] == "نابلس، شارع فيصل"
    assert [(i["variant_name"], i["quantity"], i["unit_price"]) for i in o["items"]] == [("أبيض", 2, "50.00"), ("أسود", 1, "30.00")]
    assert o["allowed_next"] == ["in_progress", "shipped", "delivered", "cancelled"]
    assert o["id"] == str(placed.order_id)


async def test_filters_and_search(db_tx):
    s, h = await setup(db_tx)
    first = await order(db_tx, s, [(s["a"], 1)])
    await order(db_tx, s, [(s["b"], 1)])
    await db_tx.execute("update orders set status = 'delivered', created_at = now() - interval '3 days' where id = $1", first.order_id)

    async def numbers(**params):
        async with client_for(db_tx) as c:
            r = await c.get("/api/orders", headers=h, params=params)
        assert r.status_code == 200
        return [o["order_number"] for o in r.json()]

    assert await numbers() == [2, 1]                       # newest first
    assert await numbers(status="delivered") == [1]
    assert await numbers(status="new") == [2]
    today = datetime.now(timezone.utc).date()
    assert await numbers(**{"from": str(today - timedelta(days=1))}) == [2]
    assert await numbers(**{"to": str(today - timedelta(days=2))}) == [1]
    assert await numbers(q="#2") == [2]
    assert await numbers(q="1") == [1]                    # short number: order number only
    assert await numbers(q="٢") == [2]                    # Arabic digit
    assert await numbers(q="ساره") == [2, 1]               # name, any spelling
    assert await numbers(q="شارع فيصل") == [2, 1]          # address
    assert await numbers(q="قميص") == [2, 1]               # product name
    assert await numbers(q="0599123") == [2, 1]            # phone
    assert await numbers(q="لا يوجد") == []


async def test_status_moves_forward_and_persists(db_tx):
    s, h = await setup(db_tx)
    placed = await order(db_tx, s, [(s["a"], 1)])
    async with client_for(db_tx) as c:
        r = await c.patch(f"/api/orders/{placed.order_id}/status", headers=h, json={"status": "in_progress"})
        assert (r.status_code, r.json()["status"], r.json()["allowed_next"]) == (200, "in_progress", ["shipped", "delivered", "cancelled"])
        r = await c.patch(f"/api/orders/{placed.order_id}/status", headers=h, json={"status": "shipped"})
        assert (r.json()["status"], r.json()["allowed_next"]) == ("shipped", ["delivered", "cancelled"])
        r = await c.patch(f"/api/orders/{placed.order_id}/status", headers=h, json={"status": "delivered"})
        assert (r.json()["status"], r.json()["allowed_next"]) == ("delivered", ["returned"])   # only a return now (3.11)
        listed = (await c.get("/api/orders", headers=h)).json()
        assert listed[0]["status"] == "delivered"
        # No going back, no cancelling a delivered order.
        for back in ("new", "in_progress", "shipped", "cancelled"):
            r = await c.patch(f"/api/orders/{placed.order_id}/status", headers=h, json={"status": back})
            assert r.status_code == 409 and "تم التوصيل" in r.json()["detail"]
    assert await stock(db_tx, s["a"]) == 9                 # delivered: stock stays deducted


async def test_cancel_returns_stock_once(db_tx):
    s, h = await setup(db_tx)
    placed = await order(db_tx, s, [(s["a"], 3), (s["b"], 2)])
    assert (await stock(db_tx, s["a"]), await stock(db_tx, s["b"])) == (7, 8)
    async with client_for(db_tx) as c:
        r = await c.patch(f"/api/orders/{placed.order_id}/status", headers=h, json={"status": "cancelled"})
        assert (r.status_code, r.json()["status"]) == (200, "cancelled")
        again = await c.patch(f"/api/orders/{placed.order_id}/status", headers=h, json={"status": "cancelled"})
        assert again.status_code == 200                   # double click: no-op
        r = await c.patch(f"/api/orders/{placed.order_id}/status", headers=h, json={"status": "new"})
        assert r.status_code == 409

    assert (await stock(db_tx, s["a"]), await stock(db_tx, s["b"])) == (10, 10)   # returned exactly once
    moves = await db_tx.fetch(
        "select change from inventory_movements where order_id = $1 and reason = 'order_cancel' order by change", placed.order_id)
    assert [m["change"] for m in moves] == [2, 3]


async def test_cancel_after_variant_deactivated_still_returns_stock(db_tx):
    s, h = await setup(db_tx)
    placed = await order(db_tx, s, [(s["a"], 4)])
    await db_tx.execute("update product_variants set is_active = false where id = $1", s["a"])
    async with client_for(db_tx) as c:
        r = await c.patch(f"/api/orders/{placed.order_id}/status", headers=h, json={"status": "cancelled"})
    assert r.status_code == 200
    assert await stock(db_tx, s["a"]) == 10


async def test_cancel_after_variant_deleted_still_cancels(db_tx):
    s, h = await setup(db_tx)
    placed = await order(db_tx, s, [(s["a"], 1), (s["b"], 1)])
    await db_tx.execute("delete from product_variants where id = $1", s["a"])   # order_items.variant_id -> null
    async with client_for(db_tx) as c:
        r = await c.patch(f"/api/orders/{placed.order_id}/status", headers=h, json={"status": "cancelled"})
        assert r.status_code == 200
        assert len(r.json()["items"]) == 2                 # snapshot still shows both
    assert await stock(db_tx, s["b"]) == 10


async def test_other_store_orders_are_invisible(db_tx):
    s, h = await setup(db_tx)
    other, h_other = await setup(db_tx)
    theirs = await order(db_tx, other, [(other["a"], 1)])
    async with client_for(db_tx) as c:
        assert (await c.get("/api/orders", headers=h)).json() == []
        r = await c.patch(f"/api/orders/{theirs.order_id}/status", headers=h, json={"status": "cancelled"})
        assert r.status_code == 404
    assert await stock(db_tx, other["a"]) == 9             # untouched


@pytest.mark.parametrize("body", [{"status": "lost"}, {"status": None}, {}, {"status": "cancelled", "store_id": str(uuid.uuid4())}])
async def test_status_validation(db_tx, body):
    s, h = await setup(db_tx)
    placed = await order(db_tx, s, [(s["a"], 1)])
    async with client_for(db_tx) as c:
        r = await c.patch(f"/api/orders/{placed.order_id}/status", headers=h, json=body)
    assert r.status_code == 422


async def test_list_validation_and_login(db_tx):
    s, h = await setup(db_tx)
    async with client_for(db_tx) as c:
        assert (await c.get("/api/orders", headers=h, params={"status": "lost"})).status_code == 422
        assert (await c.get("/api/orders", headers=h, params={"from": "yesterday"})).status_code == 422
        assert (await c.get("/api/orders", headers=h, params={"limit": 1000})).status_code == 422
        assert (await c.get("/api/orders")).status_code == 401
        assert (await c.patch(f"/api/orders/{uuid.uuid4()}/status", json={"status": "cancelled"})).status_code == 401
