"""Owner stock chat (TASKS.md 3.3): propose -> confirm, never a write before confirm."""

import pytest

from app.llm.client import LLMError
from app.llm.owner_tools import GetStockStatus, ProposeStockAdjustment
from tests.authutil import ScriptedLLM, bearer, client_for, make_token, patch_jwks
from tests.test_product_matching import store_with_sample

pytestmark = pytest.mark.db

REDUCE = "نقّص الفستان الأحمر M بمقدار 5"


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch):
    patch_jwks(monkeypatch)


def adjust(product="فستان", color=None, size=None, change=None, set_to=None):
    return ProposeStockAdjustment(product_text=product, color=color, size=size, change=change, set_to=set_to)


async def owner_of(conn, store):
    return await conn.fetchval("select owner_id from stores where id = $1", store)


async def red_m(conn, store):
    return await conn.fetchrow(
        """select v.id, v.stock_quantity from product_variants v join products p on p.id = v.product_id
            where v.store_id = $1 and p.name = 'فستان صيفي' and v.options->>'color' = 'أحمر'
              and v.options->>'size' = 'M'""", store)


async def test_propose_then_confirm_writes_chat_edit(db_tx):
    store = await store_with_sample(db_tx)
    await db_tx.execute("update product_variants set stock_quantity = 10 where id = $1", (await red_m(db_tx, store))["id"])
    llm = ScriptedLLM(tools={REDUCE: adjust(color="أحمر", size="M", change=-5)})
    h = bearer(make_token(await owner_of(db_tx, store)))
    async with client_for(db_tx, llm=llm) as c:
        r = await c.post("/api/owner-chat", headers=h, json={"text": REDUCE})
        assert r.status_code == 200
        body = r.json()
        assert (body["action"]["old_stock"], body["action"]["new_stock"]) == (10, 5)
        assert "من 10 إلى 5" in body["reply"]

        # Proposing changed nothing.
        assert (await red_m(db_tx, store))["stock_quantity"] == 10
        assert await db_tx.fetchval("select count(*) from inventory_movements where reason = 'chat_edit' and store_id = $1", store) == 0

        done = await c.post(f"/api/owner-chat/confirm/{body['action']['id']}", headers=h)
        assert done.status_code == 200 and done.json()["new_stock"] == 5
        again = await c.post(f"/api/owner-chat/confirm/{body['action']['id']}", headers=h)
        assert again.status_code == 404                                            # runs once only

    assert (await red_m(db_tx, store))["stock_quantity"] == 5
    moves = await db_tx.fetch("select change from inventory_movements where reason = 'chat_edit' and store_id = $1", store)
    assert [m["change"] for m in moves] == [-5]


async def test_negative_result_is_refused_without_a_button(db_tx):
    store = await store_with_sample(db_tx)          # red M has 4
    llm = ScriptedLLM(tools={"شيل 9": adjust(color="أحمر", size="M", change=-9)})
    async with client_for(db_tx, llm=llm) as c:
        r = (await c.post("/api/owner-chat", headers=bearer(make_token(await owner_of(db_tx, store))),
                          json={"text": "شيل 9"})).json()
    assert r["action"] is None and "ما بصير" in r["reply"]
    assert await db_tx.fetchval("select count(*) from owner_pending_actions where store_id = $1", store) == 0


async def test_missing_size_asks_instead_of_guessing(db_tx):
    store = await store_with_sample(db_tx)
    llm = ScriptedLLM(tools={"نقص الفستان الاحمر 1": adjust(color="أحمر", change=-1)})
    async with client_for(db_tx, llm=llm) as c:
        r = (await c.post("/api/owner-chat", headers=bearer(make_token(await owner_of(db_tx, store))),
                          json={"text": "نقص الفستان الاحمر 1"})).json()
    assert r["action"] is None and "المقاس" in r["reply"]


async def test_confirm_recomputes_from_current_stock(db_tx):
    """An order between propose and confirm: the result is recomputed and refused if negative."""
    store = await store_with_sample(db_tx)          # red M has 4
    llm = ScriptedLLM(tools={"نقص 3": adjust(color="أحمر", size="M", change=-3)})
    h = bearer(make_token(await owner_of(db_tx, store)))
    async with client_for(db_tx, llm=llm) as c:
        action = (await c.post("/api/owner-chat", headers=h, json={"text": "نقص 3"})).json()["action"]
        await db_tx.execute("update product_variants set stock_quantity = 1 where id = $1", (await red_m(db_tx, store))["id"])
        r = await c.post(f"/api/owner-chat/confirm/{action['id']}", headers=h)
    assert r.status_code == 409
    assert (await red_m(db_tx, store))["stock_quantity"] == 1


async def test_set_to_cancel_and_expiry(db_tx):
    store = await store_with_sample(db_tx)
    llm = ScriptedLLM(tools={"خلي 7": adjust(color="أحمر", size="M", set_to=7)})
    h = bearer(make_token(await owner_of(db_tx, store)))
    async with client_for(db_tx, llm=llm) as c:
        a = (await c.post("/api/owner-chat", headers=h, json={"text": "خلي 7"})).json()["action"]
        assert (await c.post(f"/api/owner-chat/cancel/{a['id']}", headers=h)).status_code == 204
        assert (await c.post(f"/api/owner-chat/confirm/{a['id']}", headers=h)).status_code == 404

        b = (await c.post("/api/owner-chat", headers=h, json={"text": "خلي 7"})).json()["action"]
        await db_tx.execute("update owner_pending_actions set expires_at = now() - interval '1 minute' where id = $1", b["id"])
        assert (await c.post(f"/api/owner-chat/confirm/{b['id']}", headers=h)).status_code == 410
    assert await db_tx.fetchval("select status::text from owner_pending_actions where id = $1", b["id"]) == "cancelled"
    assert (await red_m(db_tx, store))["stock_quantity"] == 4


async def test_status_lists_variants_and_other_store_cannot_confirm(db_tx):
    store = await store_with_sample(db_tx)
    other = await store_with_sample(db_tx)
    llm = ScriptedLLM(tools={"قديش باقي فستان احمر": GetStockStatus(product_text="فستان", color="أحمر", size=None),
                             "نقص 1": adjust(color="أحمر", size="M", change=-1)})
    async with client_for(db_tx, llm=llm) as c:
        r = (await c.post("/api/owner-chat", headers=bearer(make_token(await owner_of(db_tx, store))),
                          json={"text": "قديش باقي فستان احمر"})).json()
        assert "المجموع 6" in r["reply"] and r["action"] is None                 # red M 4 + red L 2

        a = (await c.post("/api/owner-chat", headers=bearer(make_token(await owner_of(db_tx, store))),
                          json={"text": "نقص 1"})).json()["action"]
        stranger = bearer(make_token(await owner_of(db_tx, other)))
        assert (await c.post(f"/api/owner-chat/confirm/{a['id']}", headers=stranger)).status_code == 404
    assert (await red_m(db_tx, store))["stock_quantity"] == 4


async def test_llm_failure_and_no_tool(db_tx):
    store = await store_with_sample(db_tx)
    llm = ScriptedLLM(tools={"boom": LLMError("timeout", "x")})
    h = bearer(make_token(await owner_of(db_tx, store)))
    async with client_for(db_tx, llm=llm) as c:
        assert (await c.post("/api/owner-chat", headers=h, json={"text": "boom"})).status_code == 503
        r = (await c.post("/api/owner-chat", headers=h, json={"text": "مرحبا"})).json()
    assert r["action"] is None and "بالمخزون" in r["reply"]
