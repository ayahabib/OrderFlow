"""Audit log (TASKS.md 3.14): every dashboard or bot change writes one row; errors write none."""

import pytest

from app.services.audit import clean
from tests.authutil import ScriptedLLM, bearer, client_for, make_token, patch_jwks
from tests.test_orders import make_store, order

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch):
    patch_jwks(monkeypatch)


async def rows(conn, store):
    return await conn.fetch(
        "select actor::text as actor, action, entity_id, before, after from audit_logs where store_id = $1 order by created_at",
        store)


async def test_owner_and_bot_changes_are_recorded(db_tx):
    s = await make_store(db_tx, stock_a=10)
    h = bearer(make_token(s["owner"]))
    placed = await order(db_tx, s, [(s["a"], 1)])                 # the bot's order
    async with client_for(db_tx) as c:
        assert (await c.patch(f"/api/orders/{placed.order_id}/status", headers=h, json={"status": "shipped"})).status_code == 200
        assert (await c.patch(f"/api/variants/{s['a']}", headers=h, json={"stock_quantity": 4})).status_code == 200
        assert (await c.patch("/api/store", headers=h, json={"name": "متجر جديد"})).status_code == 200
        audit = (await c.get("/api/audit", headers=h)).json()

    got = [(r["actor"], r["action"]) for r in await rows(db_tx, s["store"])]
    assert got == [("bot", "order.create"), ("owner", "update_status"), ("owner", "update_variant"),
                   ("owner", "update_my_store")]
    by_action = {a["action"]: a for a in audit}
    assert (by_action["update_status"]["before"], by_action["update_status"]["after"]) == ({"status": "new"}, {"status": "shipped"})
    assert by_action["update_status"]["entity_id"] == str(placed.order_id)
    assert (by_action["update_variant"]["before"], by_action["update_variant"]["after"]) == ({"stock_quantity": 9}, {"stock_quantity": 4})
    assert by_action["update_my_store"]["after"] == {"name": "متجر جديد"}
    assert by_action["order.create"]["after"]["order_number"] == placed.order_number
    assert audit[0]["action"] == "update_my_store"                # newest first


async def test_failed_or_read_only_requests_write_nothing(db_tx):
    s = await make_store(db_tx)
    h = bearer(make_token(s["owner"]))
    placed = await order(db_tx, s, [(s["a"], 1)])
    before = len(await rows(db_tx, s["store"]))
    async with client_for(db_tx, llm=ScriptedLLM(tools={})) as c:
        r = await c.patch(f"/api/orders/{placed.order_id}/status", headers=h, json={"status": "returned"})
        assert r.status_code == 409                                # not delivered yet
        assert (await c.post("/api/assistant", headers=h, json={"text": "قديش بعنا؟"})).status_code == 200
        assert (await c.get("/api/orders", headers=h)).status_code == 200
    assert len(await rows(db_tx, s["store"])) == before


async def test_other_store_cannot_read_the_log(db_tx):
    s = await make_store(db_tx)
    other = await make_store(db_tx)
    await order(db_tx, s, [(s["a"], 1)])
    async with client_for(db_tx) as c:
        assert (await c.get("/api/audit", headers=bearer(make_token(other["owner"])))).json() == []


def test_sensitive_keys_never_logged():
    assert clean({"bot_token": "x", "text": "رقمي 0599", "name": "A", "nested": {"password": "p", "ok": 1}}) == {
        "name": "A", "nested": {"ok": 1}}
