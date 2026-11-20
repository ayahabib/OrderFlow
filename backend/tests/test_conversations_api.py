"""Conversations page API: list, history, pause/resume, owner reply (TASKS.md 1.12)."""

import pytest

from tests.authutil import ScriptedLLM, analysis, bearer, client_for, make_token, patch_jwks
from tests.test_order_flow import Chat, item, new_store

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch):
    patch_jwks(monkeypatch)


async def owner_headers(conn, store):
    owner = await conn.fetchval("select owner_id from stores where id = $1", store)
    return bearer(make_token(owner))


async def only_conversation(conn, store):
    return await conn.fetchval("select id from conversations where store_id = $1", store)


ORDER_LLM = {"بدي فستانين": analysis(items=[item("فستان", qty=2)])}


async def test_done_when_paused_bot_is_silent_and_owner_reply_reaches_customer(db_tx):
    """Done-when: a paused conversation gets no bot reply; the owner's reply reaches the customer."""
    store = await new_store(db_tx)
    h = await owner_headers(db_tx, store)
    async with client_for(db_tx, llm=ScriptedLLM(ORDER_LLM)) as c:
        chat = Chat(c, store)
        assert (await chat.say("بدي فستانين"))["sender"] == "bot"
        conv = await only_conversation(db_tx, store)

        r = await c.post(f"/api/conversations/{conv}/pause", headers=h)
        assert r.status_code == 200 and r.json()["mode"] == "paused"
        assert (await chat.say("أحمر M"))["sender"] is None          # stored, but no bot reply

        r = await c.post(f"/api/conversations/{conv}/reply", headers=h, json={"text": "أهلاً، أنا صاحب المحل"})
        assert r.status_code == 200
        # The simulator (the customer's side) sees the owner's message.
        messages = (await c.get(f"/webhooks/web/{store}/messages", headers=chat.h)).json()
        assert messages[-1]["sender"] == "owner" and messages[-1]["content"] == "أهلاً، أنا صاحب المحل"

        r = await c.post(f"/api/conversations/{conv}/resume", headers=h, json={"keep_order": True})
        assert r.json()["mode"] == "bot"
        assert (await chat.say("أحمر M"))["sender"] == "bot"         # the bot answers again


async def test_owner_reply_hands_conversation_to_owner(db_tx):
    store = await new_store(db_tx)
    h = await owner_headers(db_tx, store)
    async with client_for(db_tx, llm=ScriptedLLM(ORDER_LLM)) as c:
        chat = Chat(c, store)
        await chat.say("بدي فستانين")
        conv = await only_conversation(db_tx, store)
        r = await c.post(f"/api/conversations/{conv}/reply", headers=h, json={"text": "بتصل فيك هلق"})
        assert r.json()["mode"] == "owner"
        assert [m["sender"] for m in r.json()["messages"]] == ["customer", "bot", "owner"]
        assert (await chat.say("تمام"))["sender"] is None             # bot stays quiet until resumed


async def test_escalated_conversation_listed_as_waiting(db_tx):
    store = await new_store(db_tx)
    h = await owner_headers(db_tx, store)
    async with client_for(db_tx, llm=ScriptedLLM(ORDER_LLM)) as c:
        await Chat(c, store).say("بدي احكي مع حدا")                  # keyword escalation
        await Chat(c, store).say("بدي فستانين")                      # a second customer, normal order

        listed = (await c.get("/api/conversations", headers=h)).json()
        assert sorted(x["mode"] for x in listed) == ["bot", "waiting_owner"]
        waiting = (await c.get("/api/conversations?view=waiting", headers=h)).json()
        assert len(waiting) == 1 and waiting[0]["open_escalations"] == 1
        assert waiting[0]["last_sender"] == "bot"
        assert [x["mode"] for x in (await c.get("/api/conversations?view=bot", headers=h)).json()] == ["bot"]

        found = (await c.get("/api/conversations", headers=h, params={"q": "فستانين"})).json()
        assert len(found) == 1 and found[0]["mode"] == "bot"

        detail = (await c.get(f"/api/conversations/{waiting[0]['id']}", headers=h)).json()
        assert detail["escalations"][0]["rule"] == "human_requested"
        assert detail["can_reply"] is True

        # Resuming clears the handover; the escalation itself stays for the escalations page (2.3).
        r = await c.post(f"/api/conversations/{waiting[0]['id']}/resume", headers=h)
        assert r.json()["mode"] == "bot" and r.json()["escalations"][0]["status"] == "open"


async def test_resume_keeps_or_clears_the_order_draft(db_tx):
    store = await new_store(db_tx)
    h = await owner_headers(db_tx, store)
    async with client_for(db_tx, llm=ScriptedLLM(ORDER_LLM)) as c:
        await Chat(c, store).say("بدي فستانين")
        conv = await only_conversation(db_tx, store)
        await c.post(f"/api/conversations/{conv}/pause", headers=h)

        draft = (await c.get(f"/api/conversations/{conv}", headers=h)).json()["draft"]
        assert draft["unresolved"] == 1 and draft["waiting_for"] == "variant"

        kept = (await c.post(f"/api/conversations/{conv}/resume", headers=h, json={"keep_order": True})).json()
        assert kept["draft"] == draft

        await c.post(f"/api/conversations/{conv}/pause", headers=h)
        fresh = (await c.post(f"/api/conversations/{conv}/resume", headers=h, json={"keep_order": False})).json()
        assert fresh["draft"] is None
        assert await db_tx.fetchval("select bot_state from conversations where id = $1", conv) in ("{}", {})


async def test_other_stores_conversations_are_invisible(db_tx):
    mine = await new_store(db_tx)
    other = await new_store(db_tx)
    async with client_for(db_tx, llm=ScriptedLLM(ORDER_LLM)) as c:
        await Chat(c, other).say("بدي فستانين")
        theirs = await only_conversation(db_tx, other)
        h = await owner_headers(db_tx, mine)
        assert (await c.get("/api/conversations", headers=h)).json() == []
        assert (await c.get(f"/api/conversations/{theirs}", headers=h)).status_code == 404
        for action in ("pause", "resume"):
            assert (await c.post(f"/api/conversations/{theirs}/{action}", headers=h)).status_code == 404
        r = await c.post(f"/api/conversations/{theirs}/reply", headers=h, json={"text": "هاي"})
        assert r.status_code == 404
    assert await db_tx.fetchval("select count(*) from messages where store_id = $1 and sender = 'owner'", other) == 0


async def test_reply_validation_and_closed_conversation(db_tx):
    store = await new_store(db_tx)
    h = await owner_headers(db_tx, store)
    async with client_for(db_tx, llm=ScriptedLLM(ORDER_LLM)) as c:
        await Chat(c, store).say("بدي فستانين")
        conv = await only_conversation(db_tx, store)
        assert (await c.post(f"/api/conversations/{conv}/reply", headers=h, json={"text": "   "})).status_code == 422
        assert (await c.post(f"/api/conversations/{conv}/reply", headers=h, json={"text": "x" * 2001})).status_code == 422
        assert (await c.post(f"/api/conversations/{conv}/reply", headers=h,
                             json={"text": "هاي", "store_id": str(store)})).status_code == 422  # no store_id in the body

        await db_tx.execute("update conversations set status = 'closed' where id = $1", conv)
        assert (await c.post(f"/api/conversations/{conv}/reply", headers=h, json={"text": "هاي"})).status_code == 409
        assert (await c.post(f"/api/conversations/{conv}/pause", headers=h)).status_code == 409
        assert (await c.get(f"/api/conversations/{conv}", headers=h)).json()["can_reply"] is False


async def test_needs_login(db_tx):
    async with client_for(db_tx) as c:
        assert (await c.get("/api/conversations")).status_code == 401
