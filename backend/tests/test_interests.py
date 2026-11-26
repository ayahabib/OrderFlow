""""Tell me when it's back" (TASKS.md 3.12): saved only on yes, once per customer and variant,
and the owner's click sends one fixed message per waiting customer."""

import pytest

from app.engine.order_flow import ASK_NOTIFY
from app.services.interests import back_in_stock_text
from tests.authutil import ScriptedLLM, analysis, bearer, client_for, make_token, patch_jwks
from tests.test_order_flow import Chat, item, new_store

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch):
    patch_jwks(monkeypatch)


async def sell_out_bag(conn, store):
    await conn.execute("update product_variants set stock_quantity = 0 where store_id = $1 and product_id = "
                       "(select id from products where store_id = $1 and name = 'حقيبة جلد')", store)
    return await conn.fetchval(
        "select v.id from product_variants v join products p on p.id = v.product_id "
        "where p.store_id = $1 and p.name = 'حقيبة جلد' limit 1", store)


async def owner_headers(conn, store):
    owner = await conn.fetchval("select owner_id from stores where id = $1", store)
    return bearer(make_token(owner))


async def ask_for_bag(c, store, answer):
    chat = Chat(c, store)
    r = await chat.say("بدي شنطة")
    assert ASK_NOTIFY in r["content"]
    return chat, await chat.say(answer)


async def test_yes_saves_the_interest_once(db_tx):
    store = await new_store(db_tx)
    bag = await sell_out_bag(db_tx, store)
    llm = ScriptedLLM({"بدي شنطة": analysis(items=[item("شنطة")])})
    async with client_for(db_tx, llm=llm) as c:
        chat, r = await ask_for_bag(c, store, "اه خبرني")
        assert "رح نخبرك أول ما يرجع" in r["content"]
        # Same customer asks again: still one open interest.
        r = await chat.say("بدي شنطة")
        assert ASK_NOTIFY in r["content"]
        await chat.say("نعم")
    rows = await db_tx.fetch("select variant_id from customer_interests where store_id = $1", store)
    assert [r["variant_id"] for r in rows] == [bag]


async def test_owner_sees_waiting_and_notifies_after_restock(db_tx):
    store = await new_store(db_tx)
    bag = await sell_out_bag(db_tx, store)
    llm = ScriptedLLM({"بدي شنطة": analysis(items=[item("شنطة")])})
    async with client_for(db_tx, llm=llm) as c:
        chat_a, _ = await ask_for_bag(c, store, "نعم")
        chat_b, _ = await ask_for_bag(c, store, "نعم")
        h = await owner_headers(db_tx, store)

        waiting = (await c.get("/api/interests", headers=h)).json()
        assert [(w["waiting"], w["stock_quantity"]) for w in waiting] == [(2, 0)]

        # Still sold out: nothing is sent.
        r = await c.post(f"/api/interests/{bag}/notify", headers=h)
        assert r.status_code == 409

        await db_tx.execute("update product_variants set stock_quantity = 4 where id = $1", bag)
        r = await c.post(f"/api/interests/{bag}/notify", headers=h)
        assert r.json() == {"sent": 2, "failed": 0}
        assert (await c.get("/api/interests", headers=h)).json() == []

        msgs = (await c.get(f"/webhooks/web/{store}/messages", headers=chat_a.h)).json()
        assert msgs[-1]["sender"] == "owner" and msgs[-1]["content"].startswith("خبر حلو!")

        # Sending again reaches nobody (already notified).
        assert (await c.post(f"/api/interests/{bag}/notify", headers=h)).json() == {"sent": 0, "failed": 0}


async def test_other_store_cannot_see_or_notify(db_tx):
    store = await new_store(db_tx)
    bag = await sell_out_bag(db_tx, store)
    other = await new_store(db_tx)
    llm = ScriptedLLM({"بدي شنطة": analysis(items=[item("شنطة")])})
    async with client_for(db_tx, llm=llm) as c:
        await ask_for_bag(c, store, "نعم")
        await db_tx.execute("update product_variants set stock_quantity = 4 where id = $1", bag)
        h_other = await owner_headers(db_tx, other)
        assert (await c.get("/api/interests", headers=h_other)).json() == []
        assert (await c.post(f"/api/interests/{bag}/notify", headers=h_other)).status_code == 409
    assert await db_tx.fetchval("select count(*) from customer_interests where notified_at is null") >= 1


def test_message_text():
    assert back_in_stock_text("حقيبة جلد", "الافتراضي") == "خبر حلو! حقيبة جلد رجع متوفر عنا. إذا لسا بدك ياه احكيلي وبسجّلك الطلب."
    assert "(أسود)" in back_in_stock_text("حقيبة جلد", "أسود")
