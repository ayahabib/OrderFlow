"""Order chat improvements (TASKS.md 2.6): several items in one message, edits before confirming, cancel.

Scripted LLM only. Prices in the summaries are computed by code from the sample inventory
(فستان صيفي 120, قميص قطن 60, delivery to نابلس 10).
"""

import json

import pytest

from app.engine.order_flow import EDIT_UNCLEAR
from tests.authutil import ScriptedLLM, analysis, client_for
from tests.test_order_flow import Chat, conversation, item, new_store

pytestmark = pytest.mark.db

ADDRESS = "نابلس، شارع فيصل"


async def to_summary(chat: Chat) -> dict:
    await chat.say("بدي فستان أحمر M وقميص أبيض L")
    await chat.say(ADDRESS)
    return await chat.say("0599123456")


async def test_two_items_then_edits_then_cancel(db_tx):
    store = await new_store(db_tx)
    edit = analysis(intent="edit_order")
    llm = ScriptedLLM(
        {
            "بدي فستان أحمر M وقميص أبيض L": analysis(items=[item("فستان", "أحمر", "M"), item("قميص", "أبيض", "L")]),
            ADDRESS: analysis(address_text=ADDRESS),
            "خليهم 3 فساتين": edit,
            "بدل الأحمر خليه أزرق": edit,
            "شيل القميص": edit,
        },
        edits={
            "خليهم 3 فساتين": [{"op": "set_quantity", "line": 1, "quantity": 3}],
            "بدل الأحمر خليه أزرق": [{"op": "change_options", "line": 1, "color": "أزرق"}],
            "شيل القميص": [{"op": "remove", "line": 1}],   # after the color change the shirt is line 1
        },
    )
    async with client_for(db_tx, llm=llm) as c:
        chat = Chat(c, store)
        r = await to_summary(chat)                                    # both items from one message
        assert "فستان صيفي (أحمر / M) × 1 = 120.00 ILS" in r["content"]
        assert "قميص قطن (أبيض / L) × 1 = 60.00 ILS" in r["content"]
        assert "الإجمالي: 190.00 ILS" in r["content"]

        r = await chat.say("خليهم 3 فساتين")
        assert "فستان صيفي (أحمر / M) × 3 = 360.00 ILS" in r["content"]
        assert "الإجمالي: 430.00 ILS" in r["content"]                 # recomputed by code
        assert [b["id"] for b in r["buttons"]] == ["confirm", "edit", "cancel"]

        r = await chat.say("بدل الأحمر خليه أزرق")
        assert "فستان صيفي (أزرق / M) × 3 = 360.00 ILS" in r["content"]  # size kept, color changed
        assert "أحمر" not in r["content"]

        r = await chat.say("شيل القميص")
        assert "قميص" not in r["content"] and "الإجمالي: 370.00 ILS" in r["content"]

        r = await chat.say("إلغاء")
        assert r["content"].startswith("تم إلغاء الطلب")
    state = json.loads((await conversation(db_tx, store))["bot_state"])
    assert state["items"] == [] and state["mode"] == "idle"
    assert await db_tx.fetchval("select count(*) from orders where store_id = $1", store) == 0


async def test_unclear_or_impossible_edits_ask_again(db_tx):
    store = await new_store(db_tx)
    edit = analysis(intent="edit_order")
    llm = ScriptedLLM(
        {
            "بدي فستان أحمر M وقميص أبيض L": analysis(items=[item("فستان", "أحمر", "M"), item("قميص", "أبيض", "L")]),
            ADDRESS: analysis(address_text=ADDRESS),
            "غيّر شي": edit, "شيل التالت": edit, "شيل الكل": edit,
        },
        edits={
            "شيل التالت": [{"op": "remove", "line": 3}],                 # there is no line 3
            "شيل الكل": [{"op": "remove", "line": 1}, {"op": "remove", "line": 2}],
        },
    )
    async with client_for(db_tx, llm=llm) as c:
        chat = Chat(c, store)
        await to_summary(chat)
        for text in ("غيّر شي", "شيل التالت"):
            r = await chat.say(text)
            assert r["content"].startswith(EDIT_UNCLEAR)
            assert "- فستان صيفي (أحمر / M) × 1" in r["content"]      # shows the order as it is
        r = await chat.say("شيل الكل")
        assert r["content"].startswith("شلت كل شي من الطلب")
    assert json.loads((await conversation(db_tx, store))["bot_state"])["items"] == []
