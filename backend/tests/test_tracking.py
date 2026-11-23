"""Order tracking link for the customer (TASKS.md 3.6)."""

import re

import pytest

from tests.authutil import bearer, client_for, make_token, patch_jwks
from tests.test_duplicates import scripted, to_summary
from tests.test_order_flow import Chat, conversation, escalations, new_store

pytestmark = pytest.mark.db

LINK = re.compile(r"/track/([0-9a-f]{32})")


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch):
    patch_jwks(monkeypatch)


async def test_confirmation_link_tracking_page_and_status_question(db_tx):
    store = await new_store(db_tx)
    owner = await db_tx.fetchval("select owner_id from stores where id = $1", store)
    async with client_for(db_tx, llm=scripted()) as c:
        chat = Chat(c, store)
        await to_summary(chat, "بدي فستان احمر M")
        done = (await chat.say("تأكيد"))["content"]
        token = LINK.search(done).group(1)
        assert token == await db_tx.fetchval("select tracking_token from orders where store_id = $1", store)

        page = (await c.get(f"/api/track/{token}")).json()          # no login
        assert (page["order_number"], page["status"], page["total"], page["zone_name"]) == (1, "new", "130.00", "نابلس")
        assert page["items"] == [{"product_name": "فستان صيفي", "variant_name": "أحمر / M", "quantity": 1}]
        assert not {"customer_phone", "customer_name", "delivery_address"} & page.keys()   # may be forwarded

        order_id = await db_tx.fetchval("select id from orders where store_id = $1", store)
        await c.patch(f"/api/orders/{order_id}/status", headers=bearer(make_token(owner)), json={"status": "in_progress"})
        assert (await c.get(f"/api/track/{token}")).json()["status"] == "in_progress"

        # "Where is my order?" is answered from the database, with the link; no escalation.
        r = (await chat.say("وين طلبي؟"))["content"]
        assert "طلبك #1: قيد التجهيز والتوصيل" in r and token in r
    assert await escalations(db_tx, store) == []
    assert (await conversation(db_tx, store))["status"] == "bot"


async def test_unknown_or_malformed_token(db_tx):
    async with client_for(db_tx) as c:
        assert (await c.get("/api/track/" + "0" * 32)).status_code == 404
        assert (await c.get("/api/track/not-a-token")).status_code == 422
