"""Web chat simulator limits (TASKS.md 3.8): over a limit -> 429 in Arabic, nothing stored, no LLM call."""

import pytest

from tests.authutil import ScriptedLLM, client_for, test_settings
from tests.test_web_chat import cid, new_store, send, start

pytestmark = pytest.mark.db


def limits(**values):
    base = dict(web_chat_max_messages_per_session=0, web_chat_max_messages_per_ip_hour=0,
                web_chat_max_sessions_per_ip_hour=0, web_chat_max_messages_per_store_day=0)
    base.update({f"web_chat_max_{k}": v for k, v in values.items()})
    return test_settings(**base)


async def customer_message_count(conn, store):
    return await conn.fetchval(
        """select count(*) from messages m join conversations c on c.id = m.conversation_id
            where c.store_id = $1 and m.sender = 'customer'""", store)


async def test_session_limit_refuses_without_storing_or_calling_the_llm(db_tx):
    store = await new_store(db_tx)
    llm = ScriptedLLM()
    async with client_for(db_tx, limits(messages_per_session=2), llm=llm) as c:
        h, _ = await start(c, store)
        assert (await send(c, store, h, "مرحبا")).status_code == 200
        assert (await send(c, store, h, "شو عندكم؟")).status_code == 200
        calls_before = len(llm.calls)
        r = await send(c, store, h, "بدي فستان")
        assert r.status_code == 429
        assert "ابدأ محادثة جديدة" in r.json()["detail"]
        assert len(llm.calls) == calls_before
        # A new session can go on.
        h2, _ = await start(c, store)
        assert (await send(c, store, h2, "مرحبا")).status_code == 200
    assert await customer_message_count(db_tx, store) == 3


async def test_retry_of_a_counted_message_is_not_counted_again(db_tx):
    store = await new_store(db_tx)
    async with client_for(db_tx, limits(messages_per_session=2)) as c:
        h, _ = await start(c, store)
        same = cid()
        assert (await send(c, store, h, "مرحبا", same)).json() == {"status": "received"}
        assert (await send(c, store, h, "مرحبا", same)).json() == {"status": "duplicate"}
        assert (await send(c, store, h, "شو عندكم؟")).status_code == 200
        assert (await send(c, store, h, "بدي فستان")).status_code == 429


async def test_ip_message_limit_spans_sessions(db_tx):
    store = await new_store(db_tx)
    async with client_for(db_tx, limits(messages_per_ip_hour=2)) as c:
        h1, _ = await start(c, store)
        h2, _ = await start(c, store)
        assert (await send(c, store, h1, "مرحبا")).status_code == 200
        assert (await send(c, store, h2, "مرحبا")).status_code == 200
        r = await send(c, store, h2, "شو عندكم؟")
        assert r.status_code == 429 and "كمان شوي" in r.json()["detail"]


async def test_new_sessions_per_ip_are_limited(db_tx):
    store = await new_store(db_tx)
    async with client_for(db_tx, limits(sessions_per_ip_hour=2)) as c:
        assert (await c.post(f"/webhooks/web/{store}/sessions")).status_code == 201
        assert (await c.post(f"/webhooks/web/{store}/sessions")).status_code == 201
        r = await c.post(f"/webhooks/web/{store}/sessions")
        assert r.status_code == 429 and "جلسات كتير" in r.json()["detail"]


async def test_store_daily_cap_is_per_store(db_tx):
    store, other = await new_store(db_tx), await new_store(db_tx)
    async with client_for(db_tx, limits(messages_per_store_day=1)) as c:
        h, _ = await start(c, store)
        h2, _ = await start(c, store)
        assert (await send(c, store, h, "مرحبا")).status_code == 200
        r = await send(c, store, h2, "مرحبا")
        assert r.status_code == 429 and "بكرا" in r.json()["detail"]
        # Another store has its own cap.
        ho, _ = await start(c, other)
        assert (await send(c, other, ho, "مرحبا")).status_code == 200
