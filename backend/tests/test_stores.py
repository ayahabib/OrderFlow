"""Store endpoints: create once, read, edit settings. Runs on the real DB in a rolled-back transaction."""

import uuid

import pytest

from tests.authutil import bearer, client_for, insert_auth_user, make_token, patch_jwks

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch):
    patch_jwks(monkeypatch)


async def test_register_flow_create_then_get(db_tx):
    """New user: no store -> creates one -> /api/me and GET /api/store find it."""
    user_id = await insert_auth_user(db_tx)
    h = bearer(make_token(user_id))
    async with client_for(db_tx) as c:
        assert (await c.get("/api/me", headers=h)).json()["store_id"] is None
        assert (await c.get("/api/store", headers=h)).status_code == 403

        r = await c.post("/api/store", headers=h, json={"name": "  متجر سارة  ", "phone": "0599123456"})
        assert r.status_code == 201
        store = r.json()
        assert store["name"] == "متجر سارة"  # trimmed
        assert store["currency"] == "ILS"    # default
        assert store["timezone"] == "Asia/Hebron"

        assert (await c.get("/api/me", headers=h)).json()["store_id"] == store["id"]
        assert (await c.get("/api/store", headers=h)).json()["id"] == store["id"]


async def test_cannot_create_second_store(db_tx):
    user_id = await insert_auth_user(db_tx)
    h = bearer(make_token(user_id))
    async with client_for(db_tx) as c:
        assert (await c.post("/api/store", headers=h, json={"name": "الأول"})).status_code == 201
        r = await c.post("/api/store", headers=h, json={"name": "الثاني"})
    assert r.status_code == 409
    assert await db_tx.fetchval("select count(*) from stores where owner_id = $1", user_id) == 1


async def test_edit_name_phone_currency(db_tx):
    user_id = await insert_auth_user(db_tx)
    h = bearer(make_token(user_id))
    async with client_for(db_tx) as c:
        await c.post("/api/store", headers=h, json={"name": "قديم", "phone": "0599000000"})
        r = await c.patch("/api/store", headers=h, json={"name": "جديد", "currency": "jod"})
        assert r.status_code == 200
        assert r.json()["name"] == "جديد"
        assert r.json()["currency"] == "JOD"          # uppercased
        assert r.json()["phone"] == "0599000000"      # untouched

        r = await c.patch("/api/store", headers=h, json={"phone": None})
        assert r.json()["phone"] is None              # cleared
        assert (await c.get("/api/store", headers=h)).json()["name"] == "جديد"


async def test_edit_only_touches_own_store(db_tx):
    owner_a, owner_b = await insert_auth_user(db_tx), await insert_auth_user(db_tx)
    async with client_for(db_tx) as c:
        await c.post("/api/store", headers=bearer(make_token(owner_a)), json={"name": "A"})
        await c.post("/api/store", headers=bearer(make_token(owner_b)), json={"name": "B"})
        await c.patch("/api/store", headers=bearer(make_token(owner_a)), json={"name": "A2"})
    names = await db_tx.fetch(
        "select owner_id, name from stores where owner_id = any($1::uuid[])", [owner_a, owner_b]
    )
    assert {r["owner_id"]: r["name"] for r in names} == {owner_a: "A2", owner_b: "B"}


@pytest.mark.parametrize("body", [
    {"name": "x", "store_id": str(uuid.uuid4())},   # store_id never from the body
    {"name": "x", "owner_id": str(uuid.uuid4())},
    {"name": "   "},                                 # empty after trim
    {"name": "x" * 101},
    {"name": "x", "currency": "shekel"},
    {"name": "x", "phone": "call me"},
    {},
])
async def test_create_rejects_invalid_body(db_tx, body):
    user_id = await insert_auth_user(db_tx)
    async with client_for(db_tx) as c:
        r = await c.post("/api/store", headers=bearer(make_token(user_id)), json=body)
    assert r.status_code == 422
    assert await db_tx.fetchval("select count(*) from stores where owner_id = $1", user_id) == 0


@pytest.mark.parametrize("body", [
    {"name": None},
    {"currency": None},
    {"timezone": "UTC"},           # not editable
    {"next_order_number": 1},      # not editable
    {"store_id": str(uuid.uuid4())},
])
async def test_update_rejects_invalid_body(db_tx, body):
    user_id = await insert_auth_user(db_tx)
    h = bearer(make_token(user_id))
    async with client_for(db_tx) as c:
        await c.post("/api/store", headers=h, json={"name": "ثابت"})
        r = await c.patch("/api/store", headers=h, json=body)
        assert r.status_code == 422
        assert (await c.get("/api/store", headers=h)).json()["name"] == "ثابت"


async def test_store_endpoints_require_login(db_tx):
    async with client_for(db_tx) as c:
        assert (await c.get("/api/store")).status_code == 401
        assert (await c.post("/api/store", json={"name": "x"})).status_code == 401
        assert (await c.patch("/api/store", json={"name": "x"})).status_code == 401
