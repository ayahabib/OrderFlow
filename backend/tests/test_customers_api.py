"""Customers page API (TASKS.md 3.9): one row per person, orders and spend, scoped by store."""

import pytest

from tests.authutil import bearer, client_for, make_token, patch_jwks
from tests.test_orders import make_store, order

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch):
    patch_jwks(monkeypatch)


async def add_customer(conn, store, channel, external_id, name=None, phone_normalized=None):
    return await conn.fetchval(
        """insert into customers (store_id, channel, external_id, name, phone_normalized)
           values ($1, $2, $3, $4, $5) returning id""",
        store, channel, external_id, name, phone_normalized)


async def test_order_saves_the_normalized_phone(db_tx):
    s = await make_store(db_tx)
    await order(db_tx, s, [(s["a"], 1)])  # phone "0599123456", store country code defaults to 970
    row = await db_tx.fetchrow("select phone, phone_normalized from customers where id = $1", s["customer"])
    assert (row["phone"], row["phone_normalized"]) == ("0599123456", "+970599123456")


async def test_same_phone_on_two_channels_is_one_customer(db_tx):
    s = await make_store(db_tx)
    await db_tx.execute("update customers set name = 'سارة' where id = $1", s["customer"])
    first = await order(db_tx, s, [(s["a"], 2)])            # 2*50 + 10 = 110
    tg = await add_customer(db_tx, s["store"], "telegram", "555", phone_normalized="+970599123456")
    s_tg = {**s, "customer": tg}
    await order(db_tx, s_tg, [(s["b"], 1)])                 # 30 + 10 = 40
    cancelled = await order(db_tx, s_tg, [(s["b"], 1)])
    await db_tx.execute("update orders set status = 'cancelled' where id = $1", cancelled.order_id)
    await add_customer(db_tx, s["store"], "web", "other", name="خالد")

    async with client_for(db_tx) as c:
        h = bearer(make_token(s["owner"]))
        rows = (await c.get("/api/customers", headers=h)).json()
        assert len(rows) == 2
        sara = next(r for r in rows if r["name"] == "سارة")
        assert sorted(sara["channels"]) == ["telegram", "web"]
        assert (sara["orders_count"], sara["total_spent"]) == (2, "150.00")  # cancelled one not counted
        assert sara["phone"] == "0599123456"

        detail = (await c.get(f"/api/customers/{sara['id']}", headers=h)).json()
        assert len(detail["orders"]) == 3                    # the list shows the cancelled one too
        assert detail["orders_count"] == 2 and detail["total_spent"] == "150.00"
        assert {o["order_number"] for o in detail["orders"]} >= {first.order_number}
        assert detail["address"] == "نابلس، شارع فيصل"

        # Opening the detail from the other channel's row gives the same person.
        other_row = (await c.get(f"/api/customers/{s['customer']}", headers=h)).json()
        assert len(other_row["orders"]) == 3


async def test_search_by_name_and_any_phone_format(db_tx):
    s = await make_store(db_tx)
    await db_tx.execute("update customers set name = 'سارة أحمد' where id = $1", s["customer"])
    await order(db_tx, s, [(s["a"], 1)])
    await add_customer(db_tx, s["store"], "web", "x", name="خالد")
    async with client_for(db_tx) as c:
        h = bearer(make_token(s["owner"]))
        for q in ["ساره", "0599123456", "+970 599", "123456"]:
            names = [r["name"] for r in (await c.get("/api/customers", params={"q": q}, headers=h)).json()]
            assert names == ["سارة أحمد"], q


async def test_other_store_customers_are_invisible(db_tx):
    s = await make_store(db_tx)
    other = await make_store(db_tx)
    await db_tx.execute("update customers set name = 'زبون متجر تاني' where id = $1", other["customer"])
    async with client_for(db_tx) as c:
        h = bearer(make_token(s["owner"]))
        names = [r["name"] for r in (await c.get("/api/customers", headers=h)).json()]
        assert "زبون متجر تاني" not in names
        assert (await c.get(f"/api/customers/{other['customer']}", headers=h)).status_code == 404


async def test_store_country_code_is_editable_and_validated(db_tx):
    s = await make_store(db_tx)
    async with client_for(db_tx) as c:
        h = bearer(make_token(s["owner"]))
        r = await c.patch("/api/store", json={"phone_country_code": "+962"}, headers=h)
        assert r.status_code == 200 and r.json()["phone_country_code"] == "962"
        assert (await c.patch("/api/store", json={"phone_country_code": "abc"}, headers=h)).status_code == 422
    await order(db_tx, s, [(s["a"], 1)])
    assert await db_tx.fetchval("select phone_normalized from customers where id = $1", s["customer"]) == "+962599123456"
