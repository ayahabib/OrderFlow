"""Offers API + offer prices on the products list (real DB, rolled back)."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from tests.authutil import bearer, client_for, insert_auth_user, make_token, patch_jwks

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch):
    patch_jwks(monkeypatch)


async def setup_store(conn, c):
    """Owner with a dress (2 variants) and a bag."""
    user_id = await insert_auth_user(conn)
    await conn.execute("insert into stores (owner_id, name) values ($1, 'متجر')", user_id)
    h = bearer(make_token(user_id))
    dress = (await c.post("/api/products", headers=h, json={"name": "فستان", "price": "120", "variants": [
        {"color": "أحمر", "size": "M", "quantity": 4}, {"color": "أحمر", "size": "L", "quantity": 2},
    ]})).json()
    bag = (await c.post("/api/products", headers=h, json={"name": "حقيبة", "price": "90", "variants": [{"quantity": 7}]})).json()
    return h, dress, bag


def variants_by_name(products, product_name):
    p = next(p for p in products if p["name"] == product_name)
    return {v["name"]: v for v in p["variants"]}


def iso(dt):
    return dt.isoformat()


async def test_percent_offer_on_product_shows_in_products_list(db_tx):
    async with client_for(db_tx) as c:
        h, dress, _ = await setup_store(db_tx, c)
        r = await c.post("/api/offers", headers=h, json={
            "name": "خصم الصيف", "kind": "percent_off", "product_id": dress["id"], "percent": "20",
        })
        assert r.status_code == 201
        offer = r.json()
        assert (offer["status"], offer["product_name"], offer["percent"]) == ("live", "فستان", "20.00")

        listed = (await c.get("/api/products", headers=h)).json()
    dress_variants = variants_by_name(listed, "فستان")
    assert {v["offer_price"] for v in dress_variants.values()} == {"96.00"}
    assert {v["offer_name"] for v in dress_variants.values()} == {"خصم الصيف"}
    assert variants_by_name(listed, "حقيبة")["الافتراضي"]["offer_price"] is None


async def test_variant_offer_and_cheapest_wins(db_tx):
    async with client_for(db_tx) as c:
        h, dress, _ = await setup_store(db_tx, c)
        red_m = next(v for v in dress["variants"] if v["name"] == "أحمر / M")
        await c.post("/api/offers", headers=h, json={"name": "10%", "kind": "percent_off", "product_id": dress["id"], "percent": 10})
        await c.post("/api/offers", headers=h, json={"name": "M بـ 99", "kind": "fixed_price", "variant_id": red_m["id"], "fixed_price": "99"})
        v = variants_by_name((await c.get("/api/products", headers=h)).json(), "فستان")
    assert (v["أحمر / M"]["offer_price"], v["أحمر / M"]["offer_name"]) == ("99.00", "M بـ 99")
    assert (v["أحمر / L"]["offer_price"], v["أحمر / L"]["offer_name"]) == ("108.00", "10%")


async def test_pause_schedule_and_delete(db_tx):
    async with client_for(db_tx) as c:
        h, _, bag = await setup_store(db_tx, c)
        offer = (await c.post("/api/offers", headers=h, json={
            "name": "شنط", "kind": "fixed_price", "product_id": bag["id"], "fixed_price": "75",
        })).json()

        async def bag_offer_price():
            return variants_by_name((await c.get("/api/products", headers=h)).json(), "حقيبة")["الافتراضي"]["offer_price"]

        assert await bag_offer_price() == "75.00"

        r = await c.patch(f"/api/offers/{offer['id']}", headers=h, json={"is_active": False})
        assert r.json()["status"] == "paused"
        assert await bag_offer_price() is None

        tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
        r = await c.patch(f"/api/offers/{offer['id']}", headers=h, json={"is_active": True, "starts_at": iso(tomorrow)})
        assert r.json()["status"] == "scheduled"
        assert await bag_offer_price() is None

        r = await c.patch(f"/api/offers/{offer['id']}", headers=h, json={"starts_at": None, "fixed_price": "70"})
        assert r.json()["status"] == "live"
        assert await bag_offer_price() == "70.00"

        assert (await c.delete(f"/api/offers/{offer['id']}", headers=h)).status_code == 204
        assert await bag_offer_price() is None
        assert (await c.get("/api/offers", headers=h)).json() == []


async def test_free_delivery_offer(db_tx):
    async with client_for(db_tx) as c:
        h, _, _ = await setup_store(db_tx, c)
        r = await c.post("/api/offers", headers=h, json={"name": "توصيل مجاني", "kind": "free_delivery", "min_order_total": "200"})
        assert r.status_code == 201
        assert (r.json()["min_order_total"], r.json()["product_id"]) == ("200.00", None)
        # Free delivery never changes product prices.
        listed = (await c.get("/api/products", headers=h)).json()
    assert all(v["offer_price"] is None for p in listed for v in p["variants"])


async def test_deleting_product_removes_its_offers(db_tx):
    async with client_for(db_tx) as c:
        h, _, bag = await setup_store(db_tx, c)
        await c.post("/api/offers", headers=h, json={"name": "x", "kind": "percent_off", "product_id": bag["id"], "percent": 5})
        await c.delete(f"/api/products/{bag['id']}", headers=h)
        assert (await c.get("/api/offers", headers=h)).json() == []


@pytest.mark.parametrize("make_body", [
    lambda d, v: {"name": "x", "kind": "percent_off", "product_id": d, "percent": 0},
    lambda d, v: {"name": "x", "kind": "percent_off", "product_id": d, "percent": 100},
    lambda d, v: {"name": "x", "kind": "percent_off", "product_id": d},                       # no percent
    lambda d, v: {"name": "x", "kind": "percent_off", "percent": 10},                          # no target
    lambda d, v: {"name": "x", "kind": "percent_off", "product_id": d, "variant_id": v, "percent": 10},
    lambda d, v: {"name": "x", "kind": "fixed_price", "product_id": d, "fixed_price": -1},
    lambda d, v: {"name": "x", "kind": "free_delivery", "product_id": d, "min_order_total": 100},
    lambda d, v: {"name": "x", "kind": "free_delivery"},
    lambda d, v: {"name": "x", "kind": "percent_off", "product_id": str(uuid.uuid4()), "percent": 10},  # unknown
    lambda d, v: {"name": "x", "kind": "percent_off", "product_id": d, "percent": 10,
                  "starts_at": "2026-12-10T00:00:00Z", "ends_at": "2026-12-01T00:00:00Z"},
    lambda d, v: {"name": "x", "kind": "percent_off", "product_id": d, "percent": 10, "starts_at": "2026-12-10T00:00:00"},  # no tz
    lambda d, v: {"name": " ", "kind": "percent_off", "product_id": d, "percent": 10},
    lambda d, v: {"name": "x", "kind": "bogo", "product_id": d},
    lambda d, v: {"name": "x", "kind": "percent_off", "product_id": d, "percent": 10, "store_id": str(uuid.uuid4())},
])
async def test_create_validation(db_tx, make_body):
    async with client_for(db_tx) as c:
        h, dress, _ = await setup_store(db_tx, c)
        r = await c.post("/api/offers", headers=h, json=make_body(dress["id"], dress["variants"][0]["id"]))
        assert r.status_code == 422
        assert (await c.get("/api/offers", headers=h)).json() == []


async def test_edit_validation_uses_merged_offer(db_tx):
    async with client_for(db_tx) as c:
        h, dress, _ = await setup_store(db_tx, c)
        offer = (await c.post("/api/offers", headers=h, json={
            "name": "x", "kind": "percent_off", "product_id": dress["id"], "percent": 10,
            "starts_at": "2026-12-10T00:00:00Z",
        })).json()
        bad = await c.patch(f"/api/offers/{offer['id']}", headers=h, json={"ends_at": "2026-12-01T00:00:00Z"})
        assert bad.status_code == 422
        assert (await c.patch(f"/api/offers/{offer['id']}", headers=h, json={"percent": None})).status_code == 422
        assert (await c.patch(f"/api/offers/{offer['id']}", headers=h, json={"kind": "fixed_price"})).status_code == 422


async def test_other_store_cannot_see_use_or_change_offers(db_tx):
    async with client_for(db_tx) as c:
        h_a, dress_a, _ = await setup_store(db_tx, c)
        h_b, dress_b, _ = await setup_store(db_tx, c)
        offer_b = (await c.post("/api/offers", headers=h_b, json={
            "name": "B", "kind": "percent_off", "product_id": dress_b["id"], "percent": 50,
        })).json()

        assert (await c.get("/api/offers", headers=h_a)).json() == []
        assert (await c.patch(f"/api/offers/{offer_b['id']}", headers=h_a, json={"percent": 90})).status_code == 404
        assert (await c.delete(f"/api/offers/{offer_b['id']}", headers=h_a)).status_code == 404
        # A can't attach an offer to B's product either.
        r = await c.post("/api/offers", headers=h_a, json={"name": "A", "kind": "percent_off", "product_id": dress_b["id"], "percent": 10})
        assert r.status_code == 422
        # And B's offer doesn't leak into A's prices.
        listed_a = (await c.get("/api/products", headers=h_a)).json()
        assert all(v["offer_price"] is None for p in listed_a for v in p["variants"])
        assert (await c.get("/api/offers", headers=h_b)).json()[0]["percent"] == "50.00"


async def test_offers_require_login(db_tx):
    async with client_for(db_tx) as c:
        assert (await c.get("/api/offers")).status_code == 401
        assert (await c.post("/api/offers", json={})).status_code == 401
