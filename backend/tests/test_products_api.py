"""Products page API: list with variants, edit stock/price, manual movements, store isolation."""

import uuid

import pytest

from app.services.excel_import import plan_from_file
from app.services.inventory import apply_import
from tests.authutil import bearer, client_for, insert_auth_user, make_token, patch_jwks

pytestmark = pytest.mark.db

STOCK = (
    "product_name,price,color,size,quantity,category\n"
    "فستان صيفي,120,أحمر,M,4,ملابس نسائية\n"
    "فستان صيفي,120,أحمر,L,2,ملابس نسائية\n"
    "حقيبة جلد,90,,,7,إكسسوارات\n"
).encode("utf-8")


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch):
    patch_jwks(monkeypatch)


async def store_with_stock(conn):
    user_id = await insert_auth_user(conn)
    store_id = await conn.fetchval("insert into stores (owner_id, name) values ($1, 'متجر') returning id", user_id)
    await apply_import(conn, store_id, plan_from_file("s.csv", STOCK))
    return bearer(make_token(user_id)), store_id


async def variant_id(conn, store_id, product, variant):
    return await conn.fetchval(
        """select v.id from product_variants v join products p on p.id = v.product_id
            where v.store_id = $1 and p.name = $2 and v.name = $3""",
        store_id, product, variant,
    )


async def manual_movements(conn, vid):
    return [r["change"] for r in await conn.fetch(
        "select change from inventory_movements where variant_id = $1 and reason = 'manual' order by created_at", vid
    )]


async def test_list_products_with_variants(db_tx):
    h, _ = await store_with_stock(db_tx)
    async with client_for(db_tx) as c:
        r = await c.get("/api/products", headers=h)
    assert r.status_code == 200
    products = {p["name"]: p for p in r.json()}
    assert set(products) == {"فستان صيفي", "حقيبة جلد"}
    dress = products["فستان صيفي"]
    assert dress["price"] == "120.00" and dress["category"] == "ملابس نسائية"
    variants = {v["name"]: v for v in dress["variants"]}
    assert [v["name"] for v in dress["variants"]] == ["أحمر / L", "أحمر / M"]  # sorted by name
    assert (variants["أحمر / M"]["stock_quantity"], variants["أحمر / L"]["stock_quantity"]) == (4, 2)
    assert variants["أحمر / M"]["options"] == {"color": "أحمر", "size": "M"}
    assert variants["أحمر / M"]["effective_price"] == "120.00"


async def test_edit_quantity_shows_in_list_and_records_manual_movement(db_tx):
    """TASKS.md 1.3 done-when."""
    h, store_id = await store_with_stock(db_tx)
    vid = await variant_id(db_tx, store_id, "فستان صيفي", "أحمر / M")
    async with client_for(db_tx) as c:
        r = await c.patch(f"/api/variants/{vid}", headers=h, json={"stock_quantity": 10})
        assert r.status_code == 200 and r.json()["stock_quantity"] == 10

        r = await c.patch(f"/api/variants/{vid}", headers=h, json={"stock_quantity": 7})
        assert r.json()["stock_quantity"] == 7

        r = await c.patch(f"/api/variants/{vid}", headers=h, json={"stock_quantity": 7})  # no change
        listed = (await c.get("/api/products", headers=h)).json()

    dress = next(p for p in listed if p["name"] == "فستان صيفي")
    assert {v["name"]: v["stock_quantity"] for v in dress["variants"]}["أحمر / M"] == 7
    assert await manual_movements(db_tx, vid) == [6, -3]  # 4 -> 10 -> 7; the no-op wrote nothing


async def test_price_override_set_and_clear(db_tx):
    h, store_id = await store_with_stock(db_tx)
    vid = await variant_id(db_tx, store_id, "فستان صيفي", "أحمر / L")
    async with client_for(db_tx) as c:
        r = await c.patch(f"/api/variants/{vid}", headers=h, json={"price_override": "135.50"})
        assert (r.json()["price_override"], r.json()["effective_price"]) == ("135.50", "135.50")

        r = await c.patch(f"/api/variants/{vid}", headers=h, json={"price_override": None})
        assert (r.json()["price_override"], r.json()["effective_price"]) == (None, "120.00")
    assert await manual_movements(db_tx, vid) == []  # price edits are not stock movements


async def test_edit_product_base_price(db_tx):
    h, store_id = await store_with_stock(db_tx)
    pid = await db_tx.fetchval("select id from products where store_id = $1 and name = 'حقيبة جلد'", store_id)
    async with client_for(db_tx) as c:
        r = await c.patch(f"/api/products/{pid}", headers=h, json={"price": 95})
    assert r.status_code == 200
    assert r.json()["price"] == "95.00"
    assert r.json()["variants"][0]["effective_price"] == "95.00"


async def test_cannot_see_or_edit_other_store(db_tx):
    h_a, _ = await store_with_stock(db_tx)
    _, store_b = await store_with_stock(db_tx)
    vid_b = await variant_id(db_tx, store_b, "حقيبة جلد", "الافتراضي")
    pid_b = await db_tx.fetchval("select id from products where store_id = $1 and name = 'حقيبة جلد'", store_b)

    async with client_for(db_tx) as c:
        listed = (await c.get("/api/products", headers=h_a)).json()
        assert vid_b not in {v["id"] for p in listed for v in p["variants"]}
        assert (await c.patch(f"/api/variants/{vid_b}", headers=h_a, json={"stock_quantity": 0})).status_code == 404
        assert (await c.patch(f"/api/products/{pid_b}", headers=h_a, json={"price": 1})).status_code == 404

    assert await db_tx.fetchval("select stock_quantity from product_variants where id = $1", vid_b) == 7
    assert await manual_movements(db_tx, vid_b) == []


@pytest.mark.parametrize("body", [
    {"stock_quantity": -1},
    {"stock_quantity": 2.5},
    {"stock_quantity": "lots"},
    {"stock_quantity": None},
    {"price_override": -5},
    {"price_override": "12.345"},
    {"store_id": str(uuid.uuid4())},
    {},
])
async def test_variant_edit_validation(db_tx, body):
    h, store_id = await store_with_stock(db_tx)
    vid = await variant_id(db_tx, store_id, "حقيبة جلد", "الافتراضي")
    async with client_for(db_tx) as c:
        r = await c.patch(f"/api/variants/{vid}", headers=h, json=body)
    assert r.status_code == 422
    assert await db_tx.fetchval("select stock_quantity from product_variants where id = $1", vid) == 7


async def test_unknown_variant_is_404(db_tx):
    h, _ = await store_with_stock(db_tx)
    async with client_for(db_tx) as c:
        r = await c.patch(f"/api/variants/{uuid.uuid4()}", headers=h, json={"stock_quantity": 1})
    assert r.status_code == 404
