"""Delivery zones CRUD (TASKS.md 1.4): add a zone with a fee, edit it, isolation, validation."""

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


async def test_add_and_edit_zone(db_tx):
    """Done-when: add a zone with its fee, then edit it."""
    h, _ = await owner(db_tx)
    async with client_for(db_tx) as c:
        r = await c.post("/api/delivery-zones", headers=h, json={"name": "  رام   الله ", "fee": "20"})
        assert r.status_code == 201
        zone = r.json()
        assert (zone["name"], zone["fee"], zone["is_active"]) == ("رام الله", "20.00", True)

        r = await c.patch(f"/api/delivery-zones/{zone['id']}", headers=h, json={"fee": "15.50"})
        assert r.json()["fee"] == "15.50"
        r = await c.patch(f"/api/delivery-zones/{zone['id']}", headers=h, json={"name": "رام الله والبيرة", "is_active": False})
        assert (r.json()["name"], r.json()["is_active"]) == ("رام الله والبيرة", False)

        listed = (await c.get("/api/delivery-zones", headers=h)).json()
    assert listed == [{**zone, "name": "رام الله والبيرة", "fee": "15.50", "is_active": False}]


async def test_sample_store_zones(db_tx):
    h, _ = await owner(db_tx)
    zones = [("نابلس", 10), ("رام الله", 20), ("جنين", 20), ("طولكرم", 20)]
    async with client_for(db_tx) as c:
        for name, fee in zones:
            assert (await c.post("/api/delivery-zones", headers=h, json={"name": name, "fee": fee})).status_code == 201
        listed = (await c.get("/api/delivery-zones", headers=h)).json()
    assert {z["name"]: z["fee"] for z in listed} == {n: f"{f}.00" for n, f in zones}


async def test_free_zone_fee_zero_is_allowed(db_tx):
    h, _ = await owner(db_tx)
    async with client_for(db_tx) as c:
        r = await c.post("/api/delivery-zones", headers=h, json={"name": "استلام من المحل", "fee": 0})
    assert r.status_code == 201 and r.json()["fee"] == "0.00"


async def test_duplicate_name_any_spelling_is_409(db_tx):
    h, _ = await owner(db_tx)
    async with client_for(db_tx) as c:
        first = (await c.post("/api/delivery-zones", headers=h, json={"name": "رام الله", "fee": 20})).json()
        other = (await c.post("/api/delivery-zones", headers=h, json={"name": "نابلس", "fee": 10})).json()
        assert (await c.post("/api/delivery-zones", headers=h, json={"name": "رام اللة", "fee": 5})).status_code == 409
        # Renaming into an existing name is blocked too; renaming to itself is fine.
        assert (await c.patch(f"/api/delivery-zones/{other['id']}", headers=h, json={"name": "رام الله"})).status_code == 409
        assert (await c.patch(f"/api/delivery-zones/{first['id']}", headers=h, json={"name": "رام الله"})).status_code == 200


async def test_delete_zone(db_tx):
    h, _ = await owner(db_tx)
    async with client_for(db_tx) as c:
        zone = (await c.post("/api/delivery-zones", headers=h, json={"name": "جنين", "fee": 20})).json()
        assert (await c.delete(f"/api/delivery-zones/{zone['id']}", headers=h)).status_code == 204
        assert (await c.get("/api/delivery-zones", headers=h)).json() == []
        assert (await c.delete(f"/api/delivery-zones/{zone['id']}", headers=h)).status_code == 404


async def test_zone_used_by_an_order_cannot_be_deleted(db_tx):
    h, store_id = await owner(db_tx)
    async with client_for(db_tx) as c:
        zone = (await c.post("/api/delivery-zones", headers=h, json={"name": "طولكرم", "fee": 20})).json()
        customer = await db_tx.fetchval(
            "insert into customers (store_id, channel, external_id) values ($1, 'web', 'c1') returning id", store_id
        )
        await db_tx.execute(
            """insert into orders (store_id, order_number, customer_id, delivery_zone_id, delivery_fee, subtotal, total)
               values ($1, 1, $2, $3, 20, 100, 120)""",
            store_id, customer, uuid.UUID(zone["id"]),
        )
        r = await c.delete(f"/api/delivery-zones/{zone['id']}", headers=h)
        assert r.status_code == 409 and "أوقفها" in r.json()["detail"]
        # Deactivating still works, and the request after the failed delete is fine.
        r = await c.patch(f"/api/delivery-zones/{zone['id']}", headers=h, json={"is_active": False})
        assert r.status_code == 200 and r.json()["is_active"] is False


async def test_other_store_zones_are_invisible(db_tx):
    h_a, _ = await owner(db_tx)
    h_b, store_b = await owner(db_tx)
    async with client_for(db_tx) as c:
        zone_b = (await c.post("/api/delivery-zones", headers=h_b, json={"name": "نابلس", "fee": 10})).json()
        assert (await c.get("/api/delivery-zones", headers=h_a)).json() == []
        assert (await c.patch(f"/api/delivery-zones/{zone_b['id']}", headers=h_a, json={"fee": 0})).status_code == 404
        assert (await c.delete(f"/api/delivery-zones/{zone_b['id']}", headers=h_a)).status_code == 404
        # Same name in another store is fine.
        assert (await c.post("/api/delivery-zones", headers=h_a, json={"name": "نابلس", "fee": 15})).status_code == 201
    assert await db_tx.fetchval("select fee from delivery_zones where store_id = $1", store_b) == 10


@pytest.mark.parametrize("body", [
    {"name": "x", "fee": -1},
    {"name": "x", "fee": "1.234"},
    {"name": "x", "fee": "free"},
    {"name": " ", "fee": 10},
    {"name": "x"},
    {"fee": 10},
    {"name": "x", "fee": 10, "store_id": str(uuid.uuid4())},
])
async def test_create_validation(db_tx, body):
    h, _ = await owner(db_tx)
    async with client_for(db_tx) as c:
        assert (await c.post("/api/delivery-zones", headers=h, json=body)).status_code == 422
        assert (await c.get("/api/delivery-zones", headers=h)).json() == []


async def test_update_validation(db_tx):
    h, _ = await owner(db_tx)
    async with client_for(db_tx) as c:
        zone = (await c.post("/api/delivery-zones", headers=h, json={"name": "جنين", "fee": 20})).json()
        for body in ({"fee": None}, {"name": None}, {"fee": -5}, {"store_id": str(uuid.uuid4())}):
            assert (await c.patch(f"/api/delivery-zones/{zone['id']}", headers=h, json=body)).status_code == 422
        assert (await c.get("/api/delivery-zones", headers=h)).json()[0]["fee"] == "20.00"


async def test_zones_require_login(db_tx):
    async with client_for(db_tx) as c:
        assert (await c.get("/api/delivery-zones")).status_code == 401
        assert (await c.post("/api/delivery-zones", json={"name": "x", "fee": 1})).status_code == 401
