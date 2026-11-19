"""Adding products and variants by hand from the products page."""

import uuid

import pytest

from tests.authutil import bearer, client_for, insert_auth_user, make_token, patch_jwks

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch):
    patch_jwks(monkeypatch)


async def owner(conn):
    user_id = await insert_auth_user(conn)
    store_id = await conn.fetchval("insert into stores (owner_id, name) values ($1, 'متجر') returning id", user_id)
    return bearer(make_token(user_id)), store_id


async def movements(conn, store_id):
    rows = await conn.fetch(
        """select v.name, m.change, m.reason from inventory_movements m
             join product_variants v on v.id = m.variant_id where m.store_id = $1""",
        store_id,
    )
    return sorted((r["name"], r["change"], r["reason"]) for r in rows)


async def test_create_product_with_variants(db_tx):
    h, store_id = await owner(db_tx)
    body = {
        "name": "  بلوزة   شيفون ", "price": "75", "category": "ملابس نسائية",
        "variants": [
            {"color": "أبيض", "size": "M", "quantity": 3},
            {"color": "أسود", "size": "L", "quantity": 0, "price_override": "80.00", "sku": "BL-1"},
        ],
    }
    async with client_for(db_tx) as c:
        r = await c.post("/api/products", headers=h, json=body)
        assert r.status_code == 201
        product = r.json()
        listed = (await c.get("/api/products", headers=h)).json()

    assert product["name"] == "بلوزة شيفون"
    assert product["price"] == "75.00"
    variants = {v["name"]: v for v in product["variants"]}
    assert set(variants) == {"أبيض / M", "أسود / L"}
    assert variants["أسود / L"]["effective_price"] == "80.00"
    assert variants["أسود / L"]["sku"] == "BL-1"
    assert [p["id"] for p in listed] == [product["id"]]

    assert await db_tx.fetchval("select search_text from products where id = $1", uuid.UUID(product["id"])) == "بلوزه شيفون"
    # Opening stock recorded once; zero stock records nothing.
    assert await movements(db_tx, store_id) == [("أبيض / M", 3, "manual")]


async def test_simple_product_gets_default_variant(db_tx):
    h, _ = await owner(db_tx)
    async with client_for(db_tx) as c:
        r = await c.post("/api/products", headers=h, json={"name": "محفظة", "price": 40, "variants": [{"quantity": 5}]})
    assert r.status_code == 201
    assert [v["name"] for v in r.json()["variants"]] == ["الافتراضي"]


async def test_duplicate_product_name_is_409(db_tx):
    h, store_id = await owner(db_tx)
    async with client_for(db_tx) as c:
        await c.post("/api/products", headers=h, json={"name": "شنطة جلد", "price": 90, "variants": [{"quantity": 1}]})
        r = await c.post("/api/products", headers=h, json={"name": "شنطه جلد", "price": 90, "variants": [{"quantity": 1}]})
    assert r.status_code == 409
    assert await db_tx.fetchval("select count(*) from products where store_id = $1", store_id) == 1


async def test_add_variant_to_existing_product(db_tx):
    h, store_id = await owner(db_tx)
    async with client_for(db_tx) as c:
        p = (await c.post("/api/products", headers=h,
                          json={"name": "فستان", "price": 120, "variants": [{"color": "أحمر", "size": "M", "quantity": 2}]})).json()
        r = await c.post(f"/api/products/{p['id']}/variants", headers=h, json={"color": "أزرق", "size": "M", "quantity": 4})
        assert r.status_code == 201
        assert {v["name"] for v in r.json()["variants"]} == {"أحمر / M", "أزرق / M"}

        # Same variant with another spelling -> 409, nothing added.
        dup = await c.post(f"/api/products/{p['id']}/variants", headers=h, json={"color": "احمر", "size": "M", "quantity": 1})
        assert dup.status_code == 409

    assert await movements(db_tx, store_id) == [("أحمر / M", 2, "manual"), ("أزرق / M", 4, "manual")]


async def test_cannot_add_variant_to_other_store_product(db_tx):
    h_a, _ = await owner(db_tx)
    h_b, store_b = await owner(db_tx)
    async with client_for(db_tx) as c:
        p = (await c.post("/api/products", headers=h_b, json={"name": "وشاح", "price": 45, "variants": [{"quantity": 1}]})).json()
        r = await c.post(f"/api/products/{p['id']}/variants", headers=h_a, json={"color": "أحمر", "quantity": 99})
    assert r.status_code == 404
    assert await db_tx.fetchval("select count(*) from product_variants where store_id = $1", store_b) == 1


async def test_sku_clash_is_409_and_saves_nothing(db_tx):
    h, store_id = await owner(db_tx)
    async with client_for(db_tx) as c:
        await c.post("/api/products", headers=h, json={"name": "أ", "price": 1, "variants": [{"quantity": 1, "sku": "X1"}]})
        r = await c.post("/api/products", headers=h, json={"name": "ب", "price": 1, "variants": [{"quantity": 1, "sku": "X1"}]})
    assert r.status_code == 409 and "SKU" in r.json()["detail"]
    assert await db_tx.fetchval("select count(*) from products where store_id = $1", store_id) == 1


@pytest.mark.parametrize("body", [
    {"name": "x", "price": 10, "variants": []},                            # at least one variant
    {"name": "x", "price": 10},
    {"name": " ", "price": 10, "variants": [{"quantity": 1}]},
    {"name": "x", "price": -1, "variants": [{"quantity": 1}]},
    {"name": "x", "price": "1.234", "variants": [{"quantity": 1}]},
    {"name": "x", "price": 10, "variants": [{"quantity": -2}]},
    {"name": "x", "price": 10, "variants": [{"color": "أحمر", "quantity": 1}, {"color": "احمر", "quantity": 1}]},
    {"name": "x", "price": 10, "variants": [{"quantity": 1}], "store_id": str(uuid.uuid4())},
])
async def test_create_validation(db_tx, body):
    h, store_id = await owner(db_tx)
    async with client_for(db_tx) as c:
        r = await c.post("/api/products", headers=h, json=body)
    assert r.status_code == 422
    assert await db_tx.fetchval("select count(*) from products where store_id = $1", store_id) == 0


# --- delete ------------------------------------------------------------------

async def create(c, h, name, variants):
    return (await c.post("/api/products", headers=h, json={"name": name, "price": 10, "variants": variants})).json()


async def test_delete_product_removes_variants_and_movements(db_tx):
    h, store_id = await owner(db_tx)
    async with client_for(db_tx) as c:
        p = await create(c, h, "قميص", [{"color": "أبيض", "quantity": 3}, {"color": "أسود", "quantity": 2}])
        keep = await create(c, h, "بنطلون", [{"quantity": 1}])
        r = await c.delete(f"/api/products/{p['id']}", headers=h)
        assert r.status_code == 204
        listed = (await c.get("/api/products", headers=h)).json()
        assert (await c.delete(f"/api/products/{p['id']}", headers=h)).status_code == 404  # already gone

    assert [x["id"] for x in listed] == [keep["id"]]
    assert await db_tx.fetchval("select count(*) from product_variants where store_id = $1", store_id) == 1
    assert await movements(db_tx, store_id) == [("الافتراضي", 1, "manual")]


async def test_delete_variant_but_not_the_last_one(db_tx):
    h, _ = await owner(db_tx)
    async with client_for(db_tx) as c:
        p = await create(c, h, "قميص", [{"color": "أبيض", "quantity": 3}, {"color": "أسود", "quantity": 2}])
        white, black = (v["id"] for v in sorted(p["variants"], key=lambda v: v["name"] != "أبيض"))
        assert (await c.delete(f"/api/variants/{white}", headers=h)).status_code == 204
        last = await c.delete(f"/api/variants/{black}", headers=h)
        assert last.status_code == 409 and "آخر نسخة" in last.json()["detail"]
        listed = (await c.get("/api/products", headers=h)).json()
    assert [v["name"] for v in listed[0]["variants"]] == ["أسود"]


async def test_cannot_delete_other_store_items(db_tx):
    h_a, _ = await owner(db_tx)
    h_b, store_b = await owner(db_tx)
    async with client_for(db_tx) as c:
        p = await create(c, h_b, "وشاح", [{"color": "أحمر", "quantity": 1}, {"color": "أزرق", "quantity": 1}])
        assert (await c.delete(f"/api/products/{p['id']}", headers=h_a)).status_code == 404
        assert (await c.delete(f"/api/variants/{p['variants'][0]['id']}", headers=h_a)).status_code == 404
    assert await db_tx.fetchval("select count(*) from product_variants where store_id = $1", store_b) == 2
