"""Web chat simulator (TASKS.md 1.5): messages are saved, the bot echoes, duplicates are ignored."""

import uuid

import pytest

from tests.authutil import client_for, insert_auth_user

pytestmark = pytest.mark.db


async def new_store(conn):
    owner = await insert_auth_user(conn)
    return await conn.fetchval("insert into stores (owner_id, name) values ($1, 'متجر') returning id", owner)


async def start(c, store_id):
    r = await c.post(f"/webhooks/web/{store_id}/sessions")
    assert r.status_code == 201
    return {"X-Session-Token": r.json()["session_token"]}, r.json()["customer_id"]


def cid() -> str:
    return uuid.uuid4().hex


async def send(c, store_id, h, text, client_id=None, **extra):
    return await c.post(f"/webhooks/web/{store_id}/messages", headers=h,
                        json={"text": text, "client_message_id": client_id or cid(), **extra})


async def test_message_is_saved_and_bot_echoes(db_tx):
    """Done-when: a simulator message is stored and a fixed reply comes back."""
    store = await new_store(db_tx)
    async with client_for(db_tx) as c:
        h, session = await start(c, store)
        r = await send(c, store, h, "بدي فستان احمر", customer_name="سارة")
        assert r.status_code == 200 and r.json() == {"status": "received"}
        messages = (await c.get(f"/webhooks/web/{store}/messages", headers=h)).json()

    assert [m["sender"] for m in messages] == ["customer", "bot"]
    assert messages[0]["content"] == "بدي فستان احمر"
    assert "بدي فستان احمر" in messages[1]["content"]

    customer = await db_tx.fetchrow(
        "select id, name, channel::text as channel from customers where store_id = $1 and external_id = $2", store, session
    )
    assert (customer["name"], customer["channel"]) == ("سارة", "web")
    convs = await db_tx.fetch("select id, channel::text as channel, status::text as status from conversations where customer_id = $1", customer["id"])
    assert [(c["channel"], c["status"]) for c in convs] == [("web", "bot")]
    stored = await db_tx.fetchrow(
        "select metadata->>'external_message_id' as ext from messages where sender = 'customer' and conversation_id = $1",
        convs[0]["id"],
    )
    assert stored["ext"].startswith(f"web:{session}:")


async def test_same_conversation_for_later_messages(db_tx):
    store = await new_store(db_tx)
    async with client_for(db_tx) as c:
        h, _ = await start(c, store)
        await send(c, store, h, "مرحبا")
        await send(c, store, h, "شو عندكم؟")
        messages = (await c.get(f"/webhooks/web/{store}/messages", headers=h)).json()
    assert [m["sender"] for m in messages] == ["customer", "bot", "customer", "bot"]
    assert await db_tx.fetchval("select count(*) from conversations where store_id = $1", store) == 1


async def test_duplicate_message_is_ignored(db_tx):
    """Same client_message_id twice (a retry): stored once, answered once."""
    store = await new_store(db_tx)
    async with client_for(db_tx) as c:
        h, _ = await start(c, store)
        same = cid()
        assert (await send(c, store, h, "مرحبا", same)).json() == {"status": "received"}
        assert (await send(c, store, h, "مرحبا", same)).json() == {"status": "duplicate"}
        messages = (await c.get(f"/webhooks/web/{store}/messages", headers=h)).json()
    assert [m["sender"] for m in messages] == ["customer", "bot"]


async def test_same_client_id_in_two_sessions_is_not_a_duplicate(db_tx):
    store = await new_store(db_tx)
    async with client_for(db_tx) as c:
        h1, _ = await start(c, store)
        h2, _ = await start(c, store)
        same = cid()
        assert (await send(c, store, h1, "a", same)).json()["status"] == "received"
        assert (await send(c, store, h2, "b", same)).json()["status"] == "received"


async def test_paused_conversation_gets_no_bot_reply(db_tx):
    store = await new_store(db_tx)
    async with client_for(db_tx) as c:
        h, _ = await start(c, store)
        await send(c, store, h, "مرحبا")
        await db_tx.execute("update conversations set bot_paused = true where store_id = $1", store)
        await send(c, store, h, "في حدا؟")
        messages = (await c.get(f"/webhooks/web/{store}/messages", headers=h)).json()
    # The message is still stored; only the auto-reply is skipped.
    assert [m["sender"] for m in messages] == ["customer", "bot", "customer"]


async def test_sessions_only_see_their_own_messages(db_tx):
    store = await new_store(db_tx)
    async with client_for(db_tx) as c:
        h1, _ = await start(c, store)
        h2, _ = await start(c, store)
        await send(c, store, h1, "سر العميل الأول")
        assert (await c.get(f"/webhooks/web/{store}/messages", headers=h2)).json() == []


async def test_token_from_one_store_is_rejected_by_another(db_tx):
    store_a, store_b = await new_store(db_tx), await new_store(db_tx)
    async with client_for(db_tx) as c:
        h_a, _ = await start(c, store_a)
        assert (await send(c, store_b, h_a, "x")).status_code == 401
        assert (await c.get(f"/webhooks/web/{store_b}/messages", headers=h_a)).status_code == 401
    assert await db_tx.fetchval("select count(*) from messages where store_id = $1", store_b) == 0


@pytest.mark.parametrize("headers", [{}, {"X-Session-Token": "garbage"}, {"X-Session-Token": "sim-abc.def"}])
async def test_missing_or_bad_token_is_401(db_tx, headers):
    store = await new_store(db_tx)
    async with client_for(db_tx) as c:
        assert (await send(c, store, headers, "x")).status_code == 401
        assert (await c.get(f"/webhooks/web/{store}/messages", headers=headers)).status_code == 401


async def test_unknown_store_is_404(db_tx):
    async with client_for(db_tx) as c:
        assert (await c.post(f"/webhooks/web/{uuid.uuid4()}/sessions")).status_code == 404


@pytest.mark.parametrize("body", [
    {"text": "", "client_message_id": "abcdefgh"},
    {"text": "   ", "client_message_id": "abcdefgh"},
    {"text": "x" * 4001, "client_message_id": "abcdefgh"},
    {"text": "hi", "client_message_id": "short"},
    {"text": "hi", "client_message_id": "has spaces in it"},
    {"text": "hi"},
    {"text": "hi", "client_message_id": "abcdefgh", "store_id": str(uuid.uuid4())},
])
async def test_message_validation(db_tx, body):
    store = await new_store(db_tx)
    async with client_for(db_tx) as c:
        h, _ = await start(c, store)
        r = await c.post(f"/webhooks/web/{store}/messages", headers=h, json=body)
    assert r.status_code == 422
    assert await db_tx.fetchval("select count(*) from messages where store_id = $1", store) == 0
