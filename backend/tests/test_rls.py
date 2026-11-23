"""RLS isolation: a logged-in owner only sees and changes their own store's data.

We simulate a Supabase API request inside a rolled-back transaction:
`role authenticated` + JWT claims with `sub` = user id (what auth.uid() reads).
"""

import json
import uuid

import asyncpg
import pytest

pytestmark = pytest.mark.db

STORE_TABLES = [
    "store_channels", "products", "product_variants", "inventory_movements",
    "delivery_zones", "customers", "conversations", "messages", "orders",
    "order_items", "escalations", "knowledge_base", "offers", "owner_pending_actions",
]


async def act_as(conn: asyncpg.Connection, user_id: uuid.UUID | None) -> None:
    """Switch the current transaction to the API role of a user (or anon if None)."""
    if user_id is None:
        await conn.execute("set local role anon")
        await conn.execute("select set_config('request.jwt.claims', '', true)")
        return
    claims = json.dumps({"sub": str(user_id), "role": "authenticated"})
    await conn.execute("set local role authenticated")
    await conn.execute("select set_config('request.jwt.claims', $1, true)", claims)


async def seed_two_stores(conn: asyncpg.Connection) -> dict:
    """Two users, each with a store, a product and a variant. Runs as postgres."""
    data = {}
    for key in ("a", "b"):
        user_id = uuid.uuid4()
        await conn.execute(
            "insert into auth.users (id, email, aud, role) values ($1, $2, 'authenticated', 'authenticated')",
            user_id, f"rls-test-{key}-{user_id.hex[:8]}@example.test",
        )
        store_id = await conn.fetchval(
            "insert into stores (owner_id, name) values ($1, $2) returning id",
            user_id, f"متجر {key}",
        )
        product_id = await conn.fetchval(
            "insert into products (store_id, name, price) values ($1, $2, 10) returning id",
            store_id, f"منتج {key}",
        )
        await conn.execute(
            "insert into product_variants (store_id, product_id, stock_quantity) values ($1, $2, 5)",
            store_id, product_id,
        )
        data[key] = {"user": user_id, "store": store_id, "product": product_id}
    return data


async def test_owner_sees_only_own_store(db_tx):
    data = await seed_two_stores(db_tx)
    await act_as(db_tx, data["a"]["user"])

    stores = await db_tx.fetch("select id from stores")
    assert [r["id"] for r in stores] == [data["a"]["store"]]

    products = await db_tx.fetch("select store_id from products")
    assert {r["store_id"] for r in products} == {data["a"]["store"]}

    variants = await db_tx.fetch("select store_id from product_variants")
    assert {r["store_id"] for r in variants} == {data["a"]["store"]}


async def test_owner_cannot_write_other_store(db_tx):
    data = await seed_two_stores(db_tx)
    await act_as(db_tx, data["a"]["user"])

    # Update on B's product silently matches 0 rows.
    result = await db_tx.execute(
        "update products set price = 0 where id = $1", data["b"]["product"]
    )
    assert result == "UPDATE 0"

    # Insert into B's store is rejected by the with-check clause.
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with db_tx.transaction():  # savepoint, so the outer tx stays usable
            await db_tx.execute(
                "insert into products (store_id, name, price) values ($1, 'دخيل', 1)",
                data["b"]["store"],
            )


async def test_anon_sees_nothing(db_tx):
    await seed_two_stores(db_tx)
    await act_as(db_tx, None)
    assert await db_tx.fetchval("select count(*) from stores") == 0
    assert await db_tx.fetchval("select count(*) from products") == 0


async def test_rls_enabled_on_every_public_table(db_tx):
    rows = await db_tx.fetch(
        "select tablename from pg_tables where schemaname = 'public' and not rowsecurity"
    )
    assert [r["tablename"] for r in rows] == []

    # Every store-scoped table has a store_isolation policy.
    policies = await db_tx.fetch(
        "select tablename from pg_policies where schemaname = 'public' and policyname = 'store_isolation'"
    )
    assert sorted(r["tablename"] for r in policies) == sorted(STORE_TABLES)
