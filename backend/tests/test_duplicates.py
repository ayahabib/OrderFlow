"""Duplicate and incomplete orders (TASKS.md 3.5, PRD 4.10)."""

import pytest

from app.engine.order_flow import ASK_ADDRESS, ASK_PHONE
from tests.authutil import ScriptedLLM, analysis, bearer, client_for, make_token, patch_jwks
from tests.test_order_flow import Chat, item, new_store

pytestmark = pytest.mark.db

ADDRESS = "نابلس، شارع فيصل"


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch):
    patch_jwks(monkeypatch)


def scripted():
    return ScriptedLLM({
        "بدي فستان احمر M": analysis(items=[item("فستان", "أحمر", "M")]),
        "بدي 2 فستان احمر M": analysis(items=[item("فستان", "أحمر", "M", qty=2)]),
        ADDRESS: analysis(address_text=ADDRESS),
    })


async def to_summary(chat: Chat, first: str) -> dict:
    """Send the order and answer address/phone until the confirm summary."""
    r = await chat.say(first)
    for _ in range(3):
        if [b["id"] for b in r["buttons"]] == ["confirm", "edit", "cancel"]:
            return r
        r = await chat.say(ADDRESS if r["content"] == ASK_ADDRESS else "0599123456" if r["content"] == ASK_PHONE else first)
    raise AssertionError(f"no summary: {r['content']}")


async def orders_of(conn, store):
    return await conn.fetch(
        "select id, order_number, possible_duplicate_of from orders where store_id = $1 order by order_number", store)


async def test_same_order_again_asks_then_flags(db_tx):
    store = await new_store(db_tx)
    owner = await db_tx.fetchval("select owner_id from stores where id = $1", store)
    async with client_for(db_tx, llm=scripted()) as c:
        chat = Chat(c, store)
        await to_summary(chat, "بدي فستان احمر M")
        assert (await chat.say("تأكيد"))["content"].startswith("تم تأكيد طلبك")

        # Same items again: the bot asks before saving anything.
        await to_summary(chat, "بدي فستان احمر M")
        r = await chat.say("تأكيد")
        assert "طلب #1" in r["content"] and [b["id"] for b in r["buttons"]] == ["yes", "no"]
        r = await chat.say("لا")
        assert "ما سجّلنا طلب جديد" in r["content"]
        assert len(await orders_of(db_tx, store)) == 1

        # Asked again, and this time it's a real new order: saved and flagged for the owner.
        await to_summary(chat, "بدي فستان احمر M")
        assert "طلب #1" in (await chat.say("تأكيد"))["content"]
        assert (await chat.say("نعم"))["content"].startswith("تم تأكيد طلبك")

        first, second = await orders_of(db_tx, store)
        assert second["possible_duplicate_of"] == first["id"] and first["possible_duplicate_of"] is None

        listed = (await c.get("/api/orders", headers=bearer(make_token(owner)))).json()
        assert [(o["order_number"], o["duplicate_of_number"]) for o in listed] == [(2, 1), (1, None)]


async def test_different_items_old_or_cancelled_orders_are_not_duplicates(db_tx):
    store = await new_store(db_tx)
    async with client_for(db_tx, llm=scripted()) as c:
        chat = Chat(c, store)
        await to_summary(chat, "بدي فستان احمر M")
        await chat.say("تأكيد")

        await to_summary(chat, "بدي 2 فستان احمر M")          # same variant, other quantity
        assert (await chat.say("تأكيد"))["content"].startswith("تم تأكيد طلبك")

        # Older than 24 hours, or cancelled: no question either.
        await db_tx.execute("update orders set created_at = now() - interval '25 hours' where store_id = $1 and order_number = 1", store)
        await db_tx.execute("update orders set status = 'cancelled' where store_id = $1 and order_number = 2", store)
        await to_summary(chat, "بدي فستان احمر M")
        assert (await chat.say("تأكيد"))["content"].startswith("تم تأكيد طلبك")
    assert all(o["possible_duplicate_of"] is None for o in await orders_of(db_tx, store))


async def test_incomplete_orders_list_stalled_drafts(db_tx):
    store = await new_store(db_tx)
    owner = await db_tx.fetchval("select owner_id from stores where id = $1", store)
    h = bearer(make_token(owner))
    async with client_for(db_tx, llm=scripted()) as c:
        chat = Chat(c, store)
        assert (await chat.say("بدي فستان احمر M"))["content"] == ASK_ADDRESS
        assert (await c.get("/api/orders/incomplete", headers=h)).json() == []    # just now: not stalled yet

        await db_tx.execute("update conversations set last_message_at = now() - interval '3 hours' where store_id = $1", store)
        [draft] = (await c.get("/api/orders/incomplete", headers=h)).json()
        assert draft["items"] == ["فستان صيفي (أحمر / M) × 1"]
        assert draft["missing"] == ["العنوان", "رقم الهاتف"]

        # Waiting for the owner already (inbox) or older than 7 days: not listed.
        await db_tx.execute("update conversations set status = 'waiting_owner' where store_id = $1", store)
        assert (await c.get("/api/orders/incomplete", headers=h)).json() == []
        await db_tx.execute("update conversations set status = 'bot', last_message_at = now() - interval '8 days' where store_id = $1", store)
        assert (await c.get("/api/orders/incomplete", headers=h)).json() == []
