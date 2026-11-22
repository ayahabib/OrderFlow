"""Answering escalated questions (TASKS.md 2.3). Done-when: the answer reaches the customer on the same channel."""

import pytest

from tests.authutil import ScriptedLLM, bearer, client_for, make_token, patch_jwks
from tests.test_order_flow import Chat, conversation, new_store
from tests.test_telegram_webhook import CHAT, SETTINGS, TelegramChat, connected_channel, telegram  # noqa: F401

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch):
    patch_jwks(monkeypatch)


async def owner_headers(conn, store):
    return bearer(make_token(await conn.fetchval("select owner_id from stores where id = $1", store)))


async def test_done_when_answer_reaches_web_customer_and_bot_resumes(db_tx):
    store = await new_store(db_tx)
    h = await owner_headers(db_tx, store)
    async with client_for(db_tx, llm=ScriptedLLM()) as c:
        chat = Chat(c, store)
        await chat.say("بدي احكي مع حدا")                                 # keyword -> escalated

        [esc] = (await c.get("/api/escalations", headers=h)).json()
        assert esc["status"] == "open" and esc["rule"] == "human_requested" and esc["question"] == "بدي احكي مع حدا"

        r = await c.post(f"/api/escalations/{esc['id']}/answer", headers=h,
                         json={"answer": "أهلاً، أنا صاحب المحل. كيف بقدر أساعدك؟", "save_to_kb": False, "then": "bot"})
        assert r.status_code == 200
        assert r.json()["mode"] == "bot" and r.json()["escalations"][0]["status"] == "answered"

        messages = (await c.get(f"/webhooks/web/{store}/messages", headers=chat.h)).json()
        assert messages[-1]["sender"] == "owner" and messages[-1]["content"].startswith("أهلاً، أنا صاحب المحل")
        assert (await chat.say("مرحبا"))["sender"] == "bot"                 # the bot answers again

        again = await c.post(f"/api/escalations/{esc['id']}/answer", headers=h,
                             json={"answer": "x", "save_to_kb": False})
        assert again.status_code == 409
        assert (await c.get("/api/escalations", headers=h)).json() == []     # nothing open any more

    row = await db_tx.fetchrow("select owner_answer, save_to_kb, answered_at from escalations where store_id = $1", store)
    assert row["save_to_kb"] is False and row["answered_at"] is not None
    assert await db_tx.fetchval("select count(*) from knowledge_base where store_id = $1", store) == 0   # private: never stored


async def test_owner_keeps_the_chat_and_choice_is_required(db_tx):
    store = await new_store(db_tx)
    h = await owner_headers(db_tx, store)
    async with client_for(db_tx, llm=ScriptedLLM()) as c:
        chat = Chat(c, store)
        await chat.say("بدي احكي مع حدا")
        conv = await db_tx.fetchval("select id from conversations where store_id = $1", store)
        # A second open question in the same chat.
        second = await db_tx.fetchval(
            "insert into escalations (store_id, conversation_id, question, reason, rule) "
            "values ($1, $2, 'في خصم؟', 'forced_rule', 'discount') returning id", store, conv)
        # (same transaction -> same created_at, so pick by id, not by order)
        [first] = [e["id"] for e in (await c.get("/api/escalations", headers=h)).json() if e["id"] != str(second)]

        # save_to_kb is a required, explicit choice.
        assert (await c.post(f"/api/escalations/{first}/answer", headers=h, json={"answer": "هلا"})).status_code == 422

        r = await c.post(f"/api/escalations/{first}/answer", headers=h,
                         json={"answer": "هلا", "save_to_kb": True, "then": "bot"})
        assert r.json()["mode"] == "owner"                                   # another question still open
        r = await c.post(f"/api/escalations/{second}/answer", headers=h,
                         json={"answer": "العروض موجودة بالصفحة", "save_to_kb": False, "then": "owner"})
        assert r.json()["mode"] == "owner"
        assert (await chat.say("تمام"))["sender"] is None                   # owner kept it: bot quiet
    assert (await conversation(db_tx, store))["status"] == "owner"
    saved = await db_tx.fetch("select save_to_kb from escalations where store_id = $1 order by created_at", store)
    assert [r["save_to_kb"] for r in saved] == [True, False]


async def test_answer_reaches_telegram_customer(db_tx, telegram):  # noqa: F811
    store = await new_store(db_tx)
    channel = await connected_channel(db_tx, store)
    h = await owner_headers(db_tx, store)
    async with client_for(db_tx, settings=SETTINGS, llm=ScriptedLLM()) as c:
        await TelegramChat(c, channel).say("بدي احكي مع حدا")
        [esc] = (await c.get("/api/escalations", headers=h)).json()
        assert esc["channel"] == "telegram"
        r = await c.post(f"/api/escalations/{esc['id']}/answer", headers=h,
                         json={"answer": "هلا، معك صاحب المحل", "save_to_kb": False})
        assert r.status_code == 200
        other = await new_store(db_tx)
        r = await c.post(f"/api/escalations/{esc['id']}/answer", headers=await owner_headers(db_tx, other),
                         json={"answer": "x", "save_to_kb": False})
        assert r.status_code == 404                                         # another store's question
    assert telegram.sent()[-1] == {"chat_id": CHAT, "text": "هلا، معك صاحب المحل", "disable_web_page_preview": True}
