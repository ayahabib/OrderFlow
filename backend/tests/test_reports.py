"""Sales dashboard data (TASKS.md 3.1). Done-when: the numbers change right after a new order."""

from decimal import Decimal

import pytest

from tests.authutil import bearer, client_for, make_token, patch_jwks
from tests.test_orders import make_store, order

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch):
    patch_jwks(monkeypatch)


async def test_report_counts_orders_and_updates_after_a_new_one(db_tx):
    s = await make_store(db_tx)          # قميص أبيض 50, أسود 30, delivery 10
    h = bearer(make_token(s["owner"]))
    async with client_for(db_tx) as c:
        empty = (await c.get("/api/reports/sales?days=7", headers=h)).json()
        assert empty["orders_count"] == 0 and empty["average_order"] is None
        assert len(empty["days"]) == 7 and all(d["orders_count"] == 0 for d in empty["days"])

        await order(db_tx, s, [(s["a"], 2), (s["b"], 1)])           # 100 + 30 + 10 = 140
        cancelled = await order(db_tx, s, [(s["a"], 1)])            # cancelled: must not count
        await db_tx.execute("update orders set status = 'cancelled' where id = $1", cancelled.order_id)

        r = (await c.get("/api/reports/sales?days=7", headers=h)).json()
        assert (r["orders_count"], Decimal(r["revenue"]), Decimal(r["average_order"])) == (1, Decimal("140.00"), Decimal("140.00"))
        assert (r["new_orders"], r["in_progress_orders"]) == (1, 0)
        today = r["days"][-1]
        assert today["day"] == r["end"] and today["orders_count"] == 1 and Decimal(today["revenue"]) == Decimal("140.00")
        assert [(p["product_name"], p["quantity"]) for p in r["top_products"]] == [("قميص", 3)]

        second = await order(db_tx, s, [(s["b"], 1)])                # a new order: 30 + 10
        await db_tx.execute("update orders set status = 'in_progress' where id = $1", second.order_id)
        r = (await c.get("/api/reports/sales?days=7", headers=h)).json()
        assert r["orders_count"] == 2 and Decimal(r["revenue"]) == Decimal("180.00")
        assert (r["new_orders"], r["in_progress_orders"]) == (1, 1)
        assert Decimal(r["average_order"]) == Decimal("90.00")

        assert (await c.get("/api/reports/sales?days=3", headers=h)).status_code == 422   # 7..365 only


async def test_report_is_per_store(db_tx):
    mine = await make_store(db_tx)
    other = await make_store(db_tx)
    await order(db_tx, other, [(other["a"], 1)])
    async with client_for(db_tx) as c:
        r = (await c.get("/api/reports/sales", headers=bearer(make_token(mine["owner"])))).json()
    assert r["orders_count"] == 0 and r["top_products"] == [] and r["new_orders"] == 0
