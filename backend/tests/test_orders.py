"""Atomic order placement (TASKS.md 1.9): success, full rollback, two orders on the last item."""

import asyncio
import os
import uuid
from decimal import Decimal

import asyncpg
import pytest

from app.services.order_totals import compute_totals
from app.services.orders import InsufficientStockError, place_order
from tests.authutil import insert_auth_user

pytestmark = pytest.mark.db


async def make_store(conn, stock_a=5, stock_b=3):
    """Store with two variants (prices 50 and 30), one zone and one customer."""
    owner = await insert_auth_user(conn)
    store = await conn.fetchval("insert into stores (owner_id, name) values ($1, 'متجر') returning id", owner)
    product = await conn.fetchval(
        "insert into products (store_id, name, search_text, price) values ($1, 'قميص', 'قميص', 50) returning id", store)
    a = await conn.fetchval(
        "insert into product_variants (store_id, product_id, name, stock_quantity) values ($1, $2, 'أبيض', $3) returning id",
        store, product, stock_a)
    b = await conn.fetchval(
        """insert into product_variants (store_id, product_id, name, stock_quantity, price_override)
           values ($1, $2, 'أسود', $3, 30) returning id""", store, product, stock_b)
    zone = await conn.fetchval("insert into delivery_zones (store_id, name, fee) values ($1, 'نابلس', 10) returning id", store)
    customer = await conn.fetchval(
        "insert into customers (store_id, channel, external_id) values ($1, 'web', 'c1') returning id", store)
    return dict(store=store, owner=owner, a=a, b=b, zone=zone, customer=customer)


async def order(conn, s, items):
    totals = await compute_totals(conn, s["store"], items, Decimal("10"))
    return await place_order(conn, s["store"], customer_id=s["customer"], conversation_id=None, totals=totals,
                             zone_id=s["zone"], address="نابلس، شارع فيصل", phone="0599123456")


async def stock(conn, vid):
    return await conn.fetchval("select stock_quantity from product_variants where id = $1", vid)


async def test_successful_order_saves_everything_and_deducts_stock(db_tx):
    s = await make_store(db_tx)
    placed = await order(db_tx, s, [(s["a"], 2), (s["b"], 1)])

    assert placed.order_number == 1
    assert placed.total == Decimal("140.00")        # 2*50 + 1*30 + 10
    row = await db_tx.fetchrow("select * from orders where id = $1", placed.order_id)
    assert (row["status"], row["subtotal"], row["delivery_fee"], row["total"]) == ("new", Decimal("130.00"), Decimal("10.00"), Decimal("140.00"))
    assert (row["delivery_zone_id"], row["delivery_address"]) == (s["zone"], "نابلس، شارع فيصل")

    items = await db_tx.fetch(
        "select variant_name, unit_price, list_price, quantity from order_items where order_id = $1 order by variant_name",
        placed.order_id)
    assert [(i["variant_name"], i["unit_price"], i["list_price"], i["quantity"]) for i in items] == [
        ("أبيض", Decimal("50.00"), Decimal("50.00"), 2), ("أسود", Decimal("30.00"), Decimal("30.00"), 1)]

    assert (await stock(db_tx, s["a"]), await stock(db_tx, s["b"])) == (3, 2)
    moves = await db_tx.fetch("select variant_id, change, reason::text as reason, order_id from inventory_movements where store_id = $1", s["store"])
    assert sorted((m["change"], m["reason"]) for m in moves) == [(-2, "order"), (-1, "order")]
    assert {m["order_id"] for m in moves} == {placed.order_id}

    phone = await db_tx.fetchval("select phone from customers where id = $1", s["customer"])
    assert phone == "0599123456"

    second = await order(db_tx, s, [(s["a"], 1)])
    assert second.order_number == 2                  # sequential per store


async def test_snapshot_keeps_price_after_product_changes(db_tx):
    s = await make_store(db_tx)
    placed = await order(db_tx, s, [(s["a"], 1)])
    await db_tx.execute("update products set price = 999, name = 'قميص جديد' where store_id = $1", s["store"])
    item = await db_tx.fetchrow("select product_name, unit_price from order_items where order_id = $1", placed.order_id)
    assert (item["product_name"], item["unit_price"]) == ("قميص", Decimal("50.00"))


async def test_insufficient_stock_rolls_back_everything(db_tx):
    s = await make_store(db_tx, stock_a=5, stock_b=1)
    with pytest.raises(InsufficientStockError) as exc:
        await order(db_tx, s, [(s["a"], 2), (s["b"], 3)])   # b has only 1
    assert (exc.value.variant_id, exc.value.wanted, exc.value.available) == (s["b"], 3, 1)

    # Nothing changed: no order, no items, no movements, stock untouched, number not used.
    assert await db_tx.fetchval("select count(*) from orders where store_id = $1", s["store"]) == 0
    assert await db_tx.fetchval("select count(*) from order_items where store_id = $1", s["store"]) == 0
    assert await db_tx.fetchval("select count(*) from inventory_movements where store_id = $1", s["store"]) == 0
    assert (await stock(db_tx, s["a"]), await stock(db_tx, s["b"])) == (5, 1)
    assert await db_tx.fetchval("select next_order_number from stores where id = $1", s["store"]) == 1


async def test_inactive_variant_cannot_be_ordered(db_tx):
    s = await make_store(db_tx)
    totals = await compute_totals(db_tx, s["store"], [(s["a"], 1)], Decimal("10"))
    await db_tx.execute("update product_variants set is_active = false where id = $1", s["a"])
    with pytest.raises(InsufficientStockError):
        await place_order(db_tx, s["store"], customer_id=s["customer"], conversation_id=None, totals=totals,
                          zone_id=s["zone"], address="x", phone=None)
    assert await stock(db_tx, s["a"]) == 5


async def test_order_for_another_store_variant_fails(db_tx):
    s = await make_store(db_tx)
    other = await make_store(db_tx)
    totals = await compute_totals(db_tx, other["store"], [(other["a"], 1)], Decimal("10"))
    # Same totals, but placed in store s: the variant isn't s's, so the stock update matches nothing.
    with pytest.raises(InsufficientStockError):
        await place_order(db_tx, s["store"], customer_id=s["customer"], conversation_id=None, totals=totals,
                          zone_id=s["zone"], address="x", phone=None)
    assert await stock(db_tx, other["a"]) == 5


# --- real concurrency: two connections, committed data, cleaned up after ------------------

@pytest.fixture
async def committed_store():
    """A store committed to the DB (concurrency needs real separate transactions). Deleted afterwards."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set (backend/.env)")

    async def connect():
        return await asyncpg.connect(url, statement_cache_size=0)

    conn = await connect()
    s = await make_store(conn, stock_a=1, stock_b=0)  # exactly ONE item left
    try:
        # A connect function, not the URL: a failing test's repr must never print the password.
        yield connect, s
    finally:
        await conn.execute("delete from stores where id = $1", s["store"])   # cascades to everything
        await conn.execute("delete from auth.users where id = $1", s["owner"])
        await conn.close()


async def test_two_orders_on_the_last_item_only_one_succeeds(committed_store):
    connect, s = committed_store
    a, b = await connect(), await connect()
    try:
        async def attempt(conn):
            try:
                return await order(conn, s, [(s["a"], 1)])
            except InsufficientStockError as exc:
                return exc

        results = await asyncio.gather(attempt(a), attempt(b))
    finally:
        await a.close()
        await b.close()

    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, InsufficientStockError)]
    assert len(successes) == 1 and len(failures) == 1
    assert failures[0].available == 0

    check = await connect()
    try:
        assert await check.fetchval("select stock_quantity from product_variants where id = $1", s["a"]) == 0
        assert await check.fetchval("select count(*) from orders where store_id = $1", s["store"]) == 1
        assert await check.fetchval(
            "select sum(change) from inventory_movements where store_id = $1 and reason = 'order'", s["store"]) == -1
    finally:
        await check.close()


def test_uuid_sort_is_deterministic():
    ids = [uuid.uuid4() for _ in range(5)]
    assert sorted(ids, key=str) == sorted(reversed(ids), key=str)
