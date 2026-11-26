"""Pickup from the store (TASKS.md 3.15): a zone with fee 0 and no delivery address."""

import pytest

from app.engine.order_flow import ASK_PHONE
from app.services.delivery import wants_pickup
from tests.authutil import ScriptedLLM, analysis, bearer, client_for, make_token, patch_jwks
from tests.test_order_flow import Chat, item, new_store

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch):
    patch_jwks(monkeypatch)


async def owner_headers(conn, store):
    return bearer(make_token(await conn.fetchval("select owner_id from stores where id = $1", store)))


async def test_pickup_zone_has_no_fee_and_only_one_per_store(db_tx):
    store = await new_store(db_tx)
    h = await owner_headers(db_tx, store)
    async with client_for(db_tx) as c:
        r = await c.post("/api/delivery-zones", headers=h, json={"name": "استلام من المحل", "fee": "15", "is_pickup": True})
        assert r.status_code == 201 and (r.json()["fee"], r.json()["is_pickup"]) == ("0.00", True)
        pickup = r.json()["id"]
        r = await c.post("/api/delivery-zones", headers=h, json={"name": "من الفرع", "fee": "0", "is_pickup": True})
        assert r.status_code == 409 and "واحد بس" in r.json()["detail"]
        r = await c.patch(f"/api/delivery-zones/{pickup}", headers=h, json={"fee": "5"})
        assert r.status_code == 422


async def test_customer_picks_up_instead_of_an_address(db_tx):
    store = await new_store(db_tx)
    await db_tx.execute(
        "insert into delivery_zones (store_id, name, fee, is_pickup) values ($1, 'استلام من المحل', 0, true)", store)
    llm = ScriptedLLM({"بدي فستانين": analysis(items=[item("فستان", qty=2)])})
    async with client_for(db_tx, llm=llm) as c:
        chat = Chat(c, store)
        await chat.say("بدي فستانين")
        r = await chat.say("أحمر M")
        assert "استلام" in r["content"]                       # the address question offers pickup
        r = await chat.say("بستلم من المحل")                   # read without the LLM
        assert r["content"] == ASK_PHONE
        r = await chat.say("0599123456")
    assert "الاستلام: من المحل، بدون أجرة توصيل" in r["content"]
    assert "الإجمالي: 240.00 ILS" in r["content"]           # 2 x 120, no delivery fee


async def test_without_a_pickup_zone_the_question_is_unchanged(db_tx):
    store = await new_store(db_tx)
    llm = ScriptedLLM({"بدي فستانين": analysis(items=[item("فستان", qty=2)])})
    async with client_for(db_tx, llm=llm) as c:
        chat = Chat(c, store)
        await chat.say("بدي فستانين")
        r = await chat.say("أحمر M")
    assert "استلام" not in r["content"]


@pytest.mark.parametrize("text,expected", [
    ("استلام", True), ("بستلم من المحل", True), ("بمر بوخده", True), ("الاستلام", True),
    ("نابلس شارع فيصل", False), ("رام الله", False),
])
def test_pickup_words(text, expected):
    assert wants_pickup(text) is expected
