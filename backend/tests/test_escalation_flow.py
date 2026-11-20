"""Forced escalation end to end + owner Telegram alert + link API (TASKS.md 1.11)."""

import pytest

from app.llm.client import LLMError
from app.services.notifications import make_link_code, read_link_code
from tests.authutil import FakeNotifier, ScriptedLLM, analysis, bearer, client_for, make_token, patch_jwks
from tests.test_order_flow import Chat, conversation, escalations, item, new_store

pytestmark = pytest.mark.db

OWNER_CHAT = 987654321


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch):
    patch_jwks(monkeypatch)


async def link_owner(conn, store, chat_id=OWNER_CHAT):
    await conn.execute("update stores set owner_telegram_chat_id = $2 where id = $1", store, chat_id)


async def owner_headers(conn, store):
    owner = await conn.fetchval("select owner_id from stores where id = $1", store)
    return bearer(make_token(owner))


@pytest.mark.parametrize("text, rule", [
    ("الطلب وصلني ناقص وفي قطعة مكسورة", "complaint"),
    ("في خصم اذا اخدت 3 قطع؟", "discount"),
    ("العطر فيه كحول؟ عندي حساسية", "allergy_ingredients"),
    ("بدي احكي مع حدا", "human_requested"),
    ("وين طلبيتي؟", "order_status"),
])
async def test_keyword_escalates_without_calling_the_llm(db_tx, text, rule):
    store = await new_store(db_tx)
    llm = ScriptedLLM()
    async with client_for(db_tx, llm=llm) as c:
        r = await Chat(c, store).say(text)
    assert r["content"]                                   # the customer is told someone will reply
    assert llm.calls == []                                # works even if the LLM is down
    assert [rule_ for rule_, _ in await escalations(db_tx, store)] == [rule]
    assert (await conversation(db_tx, store))["status"] == "waiting_owner"


async def test_keyword_wins_mid_order(db_tx):
    """A complaint in the middle of an order still goes to the owner, and the bot stops."""
    store = await new_store(db_tx)
    llm = ScriptedLLM({"بدي فستانين": analysis(items=[item("فستان", qty=2)])})
    async with client_for(db_tx, llm=llm) as c:
        chat = Chat(c, store)
        await chat.say("بدي فستانين")
        await chat.say("بدي احكي مع موظف")
        after = await chat.say("أحمر M")                   # paused: no bot reply any more
    assert await escalations(db_tx, store) == [("human_requested", "customer_requested")]
    assert after["sender"] is None


async def test_owner_gets_telegram_alert_with_history(db_tx):
    store = await new_store(db_tx)
    await link_owner(db_tx, store)
    notifier = FakeNotifier()
    llm = ScriptedLLM({"بدي فستانين": analysis(items=[item("فستان", qty=2)])})
    async with client_for(db_tx, llm=llm, notifier=notifier) as c:
        chat = Chat(c, store)
        await chat.say("بدي فستانين")
        assert notifier.sent == []                         # normal messages send nothing
        await chat.say("القطعة الي وصلتني مكسورة")
    conv_id = await db_tx.fetchval("select id from conversations where store_id = $1", store)
    [(chat_id, text)] = notifier.sent
    assert chat_id == OWNER_CHAT
    assert "السبب: شكوى" in text
    assert "العميل: بدي فستانين" in text and "العميل: القطعة الي وصلتني مكسورة" in text
    assert f"/conversations/{conv_id}" in text


async def test_llm_failure_alerts_the_owner_too(db_tx):
    store = await new_store(db_tx)
    await link_owner(db_tx, store)
    notifier = FakeNotifier()
    llm = ScriptedLLM({"بدي فستان": LLMError("timeout", "LLM call failed (timeout)")})
    async with client_for(db_tx, llm=llm, notifier=notifier) as c:
        await Chat(c, store).say("بدي فستان")
    assert [r for r, _ in await escalations(db_tx, store)] == ["llm_failure"]
    assert len(notifier.sent) == 1 and "خطأ تقني" in notifier.sent[0][1]


async def test_no_alert_when_owner_not_linked(db_tx):
    store = await new_store(db_tx)
    notifier = FakeNotifier()
    async with client_for(db_tx, notifier=notifier) as c:
        await Chat(c, store).say("بدي احكي مع حدا")
    assert notifier.sent == []
    assert len(await escalations(db_tx, store)) == 1      # still visible in the dashboard


async def test_failed_alert_keeps_escalation_and_reply(db_tx):
    store = await new_store(db_tx)
    await link_owner(db_tx, store)
    async with client_for(db_tx, notifier=FakeNotifier(fail=True)) as c:
        r = await Chat(c, store).say("بدي احكي مع حدا")
    assert r["content"]
    assert len(await escalations(db_tx, store)) == 1
    assert (await conversation(db_tx, store))["status"] == "waiting_owner"


# --- link API ------------------------------------------------------------------------------

async def test_status_when_server_has_no_bot(db_tx):
    store = await new_store(db_tx)
    h = await owner_headers(db_tx, store)
    async with client_for(db_tx) as c:
        assert (await c.get("/api/store/telegram", headers=h)).json() == {"available": False, "linked": False}
        assert (await c.post("/api/store/telegram/link", headers=h)).status_code == 503


async def test_link_verify_test_unlink(db_tx):
    store = await new_store(db_tx)
    h = await owner_headers(db_tx, store)
    notifier = FakeNotifier()
    async with client_for(db_tx, notifier=notifier) as c:
        r = await c.post("/api/store/telegram/link", headers=h)
        assert r.status_code == 200
        url = r.json()["url"]
        assert url.startswith("https://t.me/test_notify_bot?start=")
        code = url.split("start=")[1]
        assert read_link_code(notifier.link_secret, code) == store

        # Not pressed Start yet.
        assert (await c.post("/api/store/telegram/verify", headers=h)).status_code == 404
        assert (await c.post("/api/store/telegram/test", headers=h)).status_code == 409

        notifier.payloads = [(OWNER_CHAT, code)]
        r = await c.post("/api/store/telegram/verify", headers=h)
        assert r.json() == {"available": True, "linked": True}
        assert await db_tx.fetchval("select owner_telegram_chat_id from stores where id = $1", store) == OWNER_CHAT
        assert notifier.sent[-1][0] == OWNER_CHAT          # confirmation message

        assert (await c.post("/api/store/telegram/test", headers=h)).status_code == 204
        assert len(notifier.sent) == 2

        r = await c.delete("/api/store/telegram", headers=h)
        assert r.json() == {"available": True, "linked": False}
        assert await db_tx.fetchval("select owner_telegram_chat_id from stores where id = $1", store) is None


async def test_cannot_link_with_another_stores_code(db_tx):
    mine = await new_store(db_tx)
    other = await new_store(db_tx)
    notifier = FakeNotifier(payloads=[(555, make_link_code(FakeNotifier.link_secret, other))])
    async with client_for(db_tx, notifier=notifier) as c:
        r = await c.post("/api/store/telegram/verify", headers=await owner_headers(db_tx, mine))
    assert r.status_code == 404
    assert await db_tx.fetchval("select owner_telegram_chat_id from stores where id = $1", mine) is None
    assert await db_tx.fetchval("select owner_telegram_chat_id from stores where id = $1", other) is None


async def test_verify_explains_bot_with_webhook(db_tx):
    """A bot already used elsewhere (webhook set) can't read /start: say so instead of "try later"."""
    store = await new_store(db_tx)
    async with client_for(db_tx, notifier=FakeNotifier(has_webhook=True)) as c:
        r = await c.post("/api/store/telegram/verify", headers=await owner_headers(db_tx, store))
    assert r.status_code == 409
    assert "webhook" in r.json()["detail"]
