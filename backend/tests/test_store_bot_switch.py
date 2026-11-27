"""Store-wide auto-reply switch (task 5.5).

Done when: a message while the switch is off calls no LLM and gets no reply (but is stored and
alerts the owner); after switching back on the bot answers new messages only.
"""

import json
import uuid

import pytest

from app.channels.base import OutboundMessage
from app.engine import handler
from tests.authutil import FakeNotifier, ScriptedLLM, bearer, client_for, insert_auth_user, make_token, patch_jwks

pytestmark = pytest.mark.db

OWNER_CHAT = 555123


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch):
    patch_jwks(monkeypatch)


async def new_store(conn, bot_enabled=True, linked=True):
    owner = await insert_auth_user(conn)
    store = await conn.fetchval(
        "insert into stores (owner_id, name, bot_enabled, owner_telegram_chat_id) values ($1, 'متجر', $2, $3) returning id",
        owner, bot_enabled, OWNER_CHAT if linked else None,
    )
    return owner, store


async def start(c, store_id):
    r = await c.post(f"/webhooks/web/{store_id}/sessions")
    return {"X-Session-Token": r.json()["session_token"]}


async def send(c, store_id, h, text):
    r = await c.post(f"/webhooks/web/{store_id}/messages", headers=h,
                     json={"text": text, "client_message_id": uuid.uuid4().hex})
    assert r.status_code == 200
    return r.json()


async def senders(c, store_id, h):
    return [m["sender"] for m in (await c.get(f"/webhooks/web/{store_id}/messages", headers=h)).json()]


async def test_owner_can_switch_the_bot_off_and_on(db_tx):
    owner, store = await new_store(db_tx)
    h = bearer(make_token(owner))
    async with client_for(db_tx) as c:
        assert (await c.get("/api/store", headers=h)).json()["bot_enabled"] is True
        r = await c.patch("/api/store", headers=h, json={"bot_enabled": False})
        assert r.status_code == 200 and r.json()["bot_enabled"] is False
        assert (await c.patch("/api/store", headers=h, json={"bot_enabled": None})).status_code == 422
        assert (await c.patch("/api/store", headers=h, json={"bot_enabled": True})).json()["bot_enabled"] is True

    rows = await db_tx.fetch(
        "select before, after from audit_logs where store_id = $1 and action = 'update_my_store' order by created_at", store,
    )
    changes = [(json.loads(r["before"]), json.loads(r["after"])) for r in rows]
    assert changes == [({"bot_enabled": True}, {"bot_enabled": False}), ({"bot_enabled": False}, {"bot_enabled": True})]


async def test_switch_only_changes_own_store(db_tx):
    owner, store = await new_store(db_tx)
    _, other = await new_store(db_tx)
    async with client_for(db_tx) as c:
        await c.patch("/api/store", headers=bearer(make_token(owner)), json={"bot_enabled": False})
    assert await db_tx.fetchval("select bot_enabled from stores where id = $1", store) is False
    assert await db_tx.fetchval("select bot_enabled from stores where id = $1", other) is True


async def test_bot_off_stores_message_alerts_owner_and_calls_no_llm(db_tx):
    _, store = await new_store(db_tx, bot_enabled=False)
    llm, notifier = ScriptedLLM(), FakeNotifier()
    async with client_for(db_tx, llm=llm, notifier=notifier) as c:
        h = await start(c, store)
        assert (await c.get(f"/webhooks/web/{store}/conversation", headers=h)).json()["bot_replies"] is False
        assert await send(c, store, h, "بدي فستان احمر") == {"status": "received"}
        await send(c, store, h, "في حدا؟")
        assert await senders(c, store, h) == ["customer", "customer"]      # stored, no bot reply
        assert (await c.get(f"/webhooks/web/{store}/conversation", headers=h)).json()["bot_replies"] is False

    assert llm.calls == []
    conv = await db_tx.fetchval("select id from conversations where store_id = $1", store)
    assert [chat for chat, _ in notifier.sent] == [OWNER_CHAT, OWNER_CHAT]  # one alert per message
    assert "الرد الآلي موقوف" in notifier.sent[0][1]
    assert "بدي فستان احمر" in notifier.sent[0][1]
    assert f"/conversations/{conv}" in notifier.sent[0][1]


async def test_no_alert_when_bot_is_on_or_owner_not_linked(db_tx):
    _, on_store = await new_store(db_tx)
    _, unlinked = await new_store(db_tx, bot_enabled=False, linked=False)
    notifier = FakeNotifier()
    async with client_for(db_tx, notifier=notifier) as c:
        await send(c, on_store, await start(c, on_store), "مرحبا")
        h = await start(c, unlinked)
        await send(c, unlinked, h, "مرحبا")
        assert await senders(c, unlinked, h) == ["customer"]
    assert notifier.sent == []


async def test_after_switching_on_only_new_messages_are_answered(db_tx):
    owner, store = await new_store(db_tx, bot_enabled=False)
    async with client_for(db_tx) as c:
        h = await start(c, store)
        await send(c, store, h, "مرحبا")                                   # arrives while off
        await c.patch("/api/store", headers=bearer(make_token(owner)), json={"bot_enabled": True})
        assert await senders(c, store, h) == ["customer"]                  # the old one stays unanswered
        await send(c, store, h, "في حدا؟")
        assert await senders(c, store, h) == ["customer", "customer", "bot"]


class RecordingAdapter:
    channel = "web"

    def __init__(self):
        self.sent: list[OutboundMessage] = []

    async def send(self, to, message):
        self.sent.append(message)


async def test_switch_off_between_ingest_and_reply_stops_the_reply(db_tx):
    """The background step re-checks the switch, so a reply queued just before "off" is dropped."""
    _, store = await new_store(db_tx)
    async with client_for(db_tx) as c:
        h = await start(c, store)
        await send(c, store, h, "مرحبا")
    conv = await db_tx.fetchval("select id from conversations where store_id = $1", store)
    await db_tx.execute("update stores set bot_enabled = false where id = $1", store)
    adapter, llm = RecordingAdapter(), ScriptedLLM()
    await handler.respond(db_tx, store, conv, "x", "بدي فستان", adapter, llm)
    assert adapter.sent == [] and llm.calls == []
    assert await db_tx.fetchval(
        "select count(*) from messages where conversation_id = $1 and sender = 'bot'", conv) == 1   # only the first reply
