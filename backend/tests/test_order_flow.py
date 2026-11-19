"""Order flow through the web chat simulator with a scripted fake LLM (TASKS.md 1.8)."""

import json
import uuid
from pathlib import Path

import pytest

from app.engine.order_flow import ASK_ADDRESS, ASK_PHONE, WELCOME, button_action, parse_options, parse_phone, yes_no
from app.llm.client import LLMError
from app.llm.schemas import ExtractedItem
from app.services.excel_import import plan_from_file
from app.services.inventory import apply_import
from tests.authutil import ScriptedLLM, analysis, client_for, insert_auth_user

SAMPLE = Path(__file__).resolve().parents[2] / "eval" / "sample_inventory.csv"


def item(product, color=None, size=None, qty=1):
    return ExtractedItem(product_text=product, color=color, size=size, quantity=qty, notes=None)


async def new_store(conn, zones=(("نابلس", 10), ("رام الله", 20))):
    owner = await insert_auth_user(conn)
    store = await conn.fetchval("insert into stores (owner_id, name) values ($1, 'متجر') returning id", owner)
    await apply_import(conn, store, plan_from_file("s.csv", SAMPLE.read_bytes()))
    for name, fee in zones:
        await conn.execute("insert into delivery_zones (store_id, name, fee) values ($1, $2, $3)", store, name, fee)
    return store


class Chat:
    """Drives one simulator session and returns the bot's reply to each message."""

    def __init__(self, client, store):
        self.c, self.store, self.h = client, store, None

    async def say(self, text: str) -> dict:
        if self.h is None:
            r = await self.c.post(f"/webhooks/web/{self.store}/sessions")
            self.h = {"X-Session-Token": r.json()["session_token"]}
        r = await self.c.post(f"/webhooks/web/{self.store}/messages", headers=self.h,
                              json={"text": text, "client_message_id": uuid.uuid4().hex})
        assert r.status_code == 200
        messages = (await self.c.get(f"/webhooks/web/{self.store}/messages", headers=self.h)).json()
        last = messages[-1]
        return last if last["sender"] == "bot" else {"sender": None, "content": None, "buttons": []}


async def conversation(conn, store):
    return await conn.fetchrow(
        "select status::text as status, bot_state from conversations where store_id = $1", store)


async def escalations(conn, store):
    return [(r["rule"], r["reason"]) for r in await conn.fetch(
        "select rule, reason::text as reason from escalations where store_id = $1 order by created_at", store)]


# --- the TASKS.md done-when scenario --------------------------------------------------

@pytest.mark.db
async def test_full_order_from_two_dresses_to_confirmation(db_tx):
    store = await new_store(db_tx)
    llm = ScriptedLLM({
        "بدي فستانين": analysis(items=[item("فستان", qty=2)]),
        "نابلس، شارع فيصل": analysis(address_text="نابلس، شارع فيصل"),
    })
    async with client_for(db_tx, llm=llm) as c:
        chat = Chat(c, store)
        r = await chat.say("بدي فستانين")
        assert r["content"] == "أي اللون (أحمر، أزرق) والمقاس (L، M) بدك من فستان صيفي؟"

        r = await chat.say("أحمر M")                 # answered without the LLM
        assert r["content"] == ASK_ADDRESS

        r = await chat.say("نابلس، شارع فيصل")
        assert r["content"] == ASK_PHONE

        r = await chat.say("0599123456")             # answered without the LLM
        summary = r["content"]
        assert "فستان صيفي (أحمر / M) × 2 = 240.00 ILS" in summary
        assert "التوصيل (نابلس): 10.00 ILS" in summary
        assert "الإجمالي: 250.00 ILS" in summary
        assert [b["id"] for b in r["buttons"]] == ["confirm", "edit", "cancel"]

        r = await chat.say("تأكيد")
        assert r["content"].startswith("تم تأكيد طلبك ✓")
        assert "رقم الطلب: #1" in r["content"] and "الإجمالي: 250.00 ILS" in r["content"]

    # Only two messages needed the LLM; the address one was told what the bot had asked.
    assert len(llm.calls) == 2
    assert ASK_ADDRESS in llm.calls[1]["user"]

    # Task 1.9: the order is saved with the shown total, stock is deducted, the draft is cleared.
    order = await db_tx.fetchrow("select id, order_number, subtotal, delivery_fee, total, delivery_address from orders where store_id = $1", store)
    assert (order["order_number"], str(order["subtotal"]), str(order["delivery_fee"]), str(order["total"])) == (1, "240.00", "10.00", "250.00")
    assert order["delivery_address"] == "نابلس، شارع فيصل"
    stock = await db_tx.fetchval(
        "select stock_quantity from product_variants v join products p on p.id = v.product_id "
        "where v.store_id = $1 and p.name = 'فستان صيفي' and v.name = 'أحمر / M'", store)
    assert stock == 2                                   # 4 - 2
    conv = await conversation(db_tx, store)
    assert conv["status"] == "bot" and json.loads(conv["bot_state"])["items"] == []


@pytest.mark.db
async def test_offers_and_free_delivery_in_the_summary(db_tx):
    store = await new_store(db_tx)
    dress = await db_tx.fetchval("select id from products where store_id = $1 and name = 'فستان صيفي'", store)
    await db_tx.execute("insert into offers (store_id, name, kind, product_id, percent) values ($1, 'خصم الصيف', 'percent_off', $2, 20)", store, dress)
    await db_tx.execute("insert into offers (store_id, name, kind, min_order_total) values ($1, 'توصيل مجاني', 'free_delivery', 150)", store)
    llm = ScriptedLLM({"بدي فستانين احمر M لنابلس رقمي 0599123456": analysis(
        items=[item("فستان", "أحمر", "M", 2)], address_text="نابلس", phone="0599123456")})
    async with client_for(db_tx, llm=llm) as c:
        r = await Chat(c, store).say("بدي فستانين احمر M لنابلس رقمي 0599123456")
    s = r["content"]
    assert "× 2 = 192.00 ILS (عرض «خصم الصيف»: 96.00 بدل 120.00 للقطعة)" in s
    assert "التوصيل (نابلس): مجاني (عرض «توصيل مجاني»)" in s
    assert "الإجمالي: 192.00 ILS" in s


# --- missing info, stock, matching -----------------------------------------------------------

@pytest.mark.db
async def test_not_found_item_is_reported_and_the_rest_continues(db_tx):
    store = await new_store(db_tx)
    llm = ScriptedLLM({"بدي طقم رياضي وشنطة": analysis(items=[item("طقم رياضي"), item("شنطة")])})
    async with client_for(db_tx, llm=llm) as c:
        r = await Chat(c, store).say("بدي طقم رياضي وشنطة")
    assert "مش موجود" in r["content"] and r["content"].endswith(ASK_ADDRESS)


@pytest.mark.db
async def test_partial_stock_offer_yes(db_tx):
    store = await new_store(db_tx)
    llm = ScriptedLLM({"بدي 5 كوتشي ابيض 42": analysis(items=[item("كوتشي", "أبيض", "42", 5)])})
    async with client_for(db_tx, llm=llm) as c:
        chat = Chat(c, store)
        r = await chat.say("بدي 5 كوتشي ابيض 42")    # stock is 2
        assert "2 بس" in r["content"] and [b["id"] for b in r["buttons"]] == ["yes", "no"]
        r = await chat.say("نعم")
        assert r["content"] == ASK_ADDRESS
    state = (await conversation(db_tx, store))["bot_state"]
    assert '"quantity": 2' in state or '"quantity":2' in state


@pytest.mark.db
async def test_out_of_stock_item_alone(db_tx):
    store = await new_store(db_tx)
    llm = ScriptedLLM({"بدي فستان ازرق L": analysis(items=[item("فستان", "أزرق", "L")])})   # stock 0
    async with client_for(db_tx, llm=llm) as c:
        r = await Chat(c, store).say("بدي فستان ازرق L")
    assert "خلص" in r["content"] and "شو بتحب تطلب" in r["content"]


@pytest.mark.db
async def test_answer_with_words_the_parser_cannot_read_goes_to_the_llm(db_tx):
    store = await new_store(db_tx)
    llm = ScriptedLLM({
        "بدي جينز ازرق": analysis(items=[item("جينز", "أزرق")]),
        "خليه تنين وتلاتين": analysis(items=[item("جينز", None, "32")]),
    })
    async with client_for(db_tx, llm=llm) as c:
        chat = Chat(c, store)
        r = await chat.say("بدي جينز ازرق")
        assert r["content"] == "أي المقاس (30، 32) بدك من بنطلون جينز؟"
        r = await chat.say("خليه تنين وتلاتين")
        assert r["content"] == ASK_ADDRESS
    assert "أي المقاس" in llm.calls[1]["user"]  # context was given


# --- zones -------------------------------------------------------------------------------

@pytest.mark.db
async def test_area_outside_zones_is_declined_not_escalated(db_tx):
    """Owner's choice: out-of-area orders end with a polite no, without involving the owner."""
    store = await new_store(db_tx)
    llm = ScriptedLLM({"بدي شنطة": analysis(items=[item("شنطة")]),
                       "الخليل، دوار ابن رشد": analysis(address_text="الخليل، دوار ابن رشد")})
    async with client_for(db_tx, llm=llm) as c:
        chat = Chat(c, store)
        await chat.say("بدي شنطة")
        r = await chat.say("الخليل، دوار ابن رشد")
        assert r["content"].startswith("لأي منطقة التوصيل؟") and "نابلس" in r["content"]
        r = await chat.say("الخليل")
        assert r["content"].startswith("للأسف ما منوصّل على هالمنطقة")
        assert "رام الله، نابلس" in r["content"]          # our zones, alphabetical
        assert "\n" in r["content"] and "\\n" not in r["content"]  # a real line break, not a literal "\n"
        r = await chat.say("مرحبا")                  # the bot is still active for a new order
        assert r["content"] == WELCOME
    assert await escalations(db_tx, store) == []
    conv = await conversation(db_tx, store)
    assert conv["status"] == "bot"
    assert json.loads(conv["bot_state"])["items"] == []  # the order was closed


@pytest.mark.db
async def test_long_reply_outside_zones_is_declined_too(db_tx):
    store = await new_store(db_tx)
    llm = ScriptedLLM({"بدي شنطة": analysis(items=[item("شنطة")]),
                       "الخليل": analysis(address_text="الخليل"),
                       "انا ساكن بالخليل جنب الجامعة": analysis(intent="other")})
    async with client_for(db_tx, llm=llm) as c:
        chat = Chat(c, store)
        await chat.say("بدي شنطة")
        await chat.say("الخليل")                     # address given: not a zone -> list zones
        r = await chat.say("انا ساكن بالخليل جنب الجامعة")   # long reply, still no zone
        assert r["content"].startswith("للأسف ما منوصّل")
    assert await escalations(db_tx, store) == []


@pytest.mark.db
async def test_choosing_a_listed_zone_continues(db_tx):
    store = await new_store(db_tx)
    llm = ScriptedLLM({"بدي شنطة": analysis(items=[item("شنطة")]),
                       "حي المعاجين": analysis(address_text="حي المعاجين")})
    async with client_for(db_tx, llm=llm) as c:
        chat = Chat(c, store)
        await chat.say("بدي شنطة")
        await chat.say("حي المعاجين")
        r = await chat.say("نابلس")
        assert r["content"] == ASK_PHONE


@pytest.mark.db
async def test_store_without_zones_never_invents_a_fee(db_tx):
    store = await new_store(db_tx, zones=())
    llm = ScriptedLLM({"بدي شنطة لنابلس": analysis(items=[item("شنطة")], address_text="نابلس")})
    async with client_for(db_tx, llm=llm) as c:
        r = await Chat(c, store).say("بدي شنطة لنابلس")
    assert "مش من المناطق المسجلة" in r["content"]
    assert await escalations(db_tx, store) == [("unknown_zone", "forced_rule")]


# --- escalations -------------------------------------------------------------------------------

@pytest.mark.db
@pytest.mark.parametrize("reason, expected_reason", [
    ("complaint", "forced_rule"), ("discount", "forced_rule"), ("allergy_ingredients", "forced_rule"),
    ("human_requested", "customer_requested"), ("order_status", "forced_rule"),
])
async def test_sensitive_messages_go_to_owner(db_tx, reason, expected_reason):
    store = await new_store(db_tx)
    llm = ScriptedLLM({"رسالة": analysis(intent="other", sensitive_reason=reason)})
    async with client_for(db_tx, llm=llm) as c:
        r = await Chat(c, store).say("رسالة")
    assert r["content"]
    assert await escalations(db_tx, store) == [(reason, expected_reason)]
    assert (await conversation(db_tx, store))["status"] == "waiting_owner"


@pytest.mark.db
async def test_llm_failure_keeps_message_and_goes_to_owner(db_tx):
    store = await new_store(db_tx)
    llm = ScriptedLLM({"بدي فستان": LLMError("timeout", "LLM call failed (timeout)")})
    async with client_for(db_tx, llm=llm) as c:
        r = await Chat(c, store).say("بدي فستان")
    assert "حوّلتها لصاحب المتجر" in r["content"]
    assert await escalations(db_tx, store) == [("llm_failure", "forced_rule")]
    assert await db_tx.fetchval("select count(*) from messages where store_id = $1 and sender = 'customer'", store) == 1


@pytest.mark.db
async def test_general_question_goes_to_owner_until_rag(db_tx):
    store = await new_store(db_tx)
    llm = ScriptedLLM({"شو ساعات الدوام؟": analysis(intent="question")})
    async with client_for(db_tx, llm=llm) as c:
        await Chat(c, store).say("شو ساعات الدوام؟")
    assert await escalations(db_tx, store) == [("not_in_data", "low_confidence")]


@pytest.mark.db
async def test_large_order_goes_to_owner(db_tx):
    store = await new_store(db_tx)
    llm = ScriptedLLM({"بدي 10 قمصان ابيض M": analysis(items=[item("قميص", "أبيض", "M", 10)])})  # item limit 10
    async with client_for(db_tx, llm=llm) as c:
        await Chat(c, store).say("بدي 10 قمصان ابيض M")
    assert await escalations(db_tx, store) == [("large_order", "forced_rule")]


@pytest.mark.db
async def test_greeting_gets_welcome_without_escalation(db_tx):
    store = await new_store(db_tx)
    async with client_for(db_tx, llm=ScriptedLLM()) as c:
        r = await Chat(c, store).say("مرحبا")
    assert r["content"] == WELCOME
    assert await escalations(db_tx, store) == []


# --- buttons ------------------------------------------------------------------------------

@pytest.mark.db
async def test_cancel_resets_and_edit_reopens(db_tx):
    store = await new_store(db_tx)
    llm = ScriptedLLM({
        "بدي شنطة لنابلس 0599123456": analysis(items=[item("شنطة")], address_text="نابلس", phone="0599123456"),
        "زيد شال": analysis(intent="edit_order", items=[item("شال")]),
    })
    async with client_for(db_tx, llm=llm) as c:
        chat = Chat(c, store)
        r = await chat.say("بدي شنطة لنابلس 0599123456")
        assert "الإجمالي: 100.00 ILS" in r["content"]          # 90 + 10
        r = await chat.say("تعديل")
        assert "شو بدك تعدّل" in r["content"]
        r = await chat.say("زيد شال")
        assert "وشاح حرير × 1 = 45.00 ILS" in r["content"] and "الإجمالي: 145.00 ILS" in r["content"]
        r = await chat.say("إلغاء")
        assert "تم إلغاء الطلب" in r["content"]
    assert (await conversation(db_tx, store))["bot_state"] in ('{}', '{"mode": "idle", "items": [], "queue": [], "pending": null, "address": null, "zone_id": null, "zone_name": null, "phone": null}') \
        or '"mode":"idle"' in (await conversation(db_tx, store))["bot_state"].replace(" ", "")


# --- small parsers (no database) ---------------------------------------------------------------

def test_buttons_yes_no_phone():
    assert [button_action(t) for t in ("تأكيد", "تاكيد", "إلغاء", "تعديل", "تأكيد الطلب")] == ["confirm", "confirm", "cancel", "edit", None]
    assert [yes_no(t) for t in ("نعم", "اه تمام", "لا", "لأ شكرا", "بدي اشي تاني كتير")] == [True, True, False, False, None]
    assert parse_phone("٠٥٩٩ ١٢٣ ٤٥٦") == "0599123456"
    assert parse_phone("+970-599-123456") == "+970599123456"
    assert parse_phone("نابلس") is None and parse_phone("123") is None


def test_parse_options():
    values = {"color": ["أحمر", "أزرق"], "size": ["L", "M"]}
    assert parse_options("أحمر M", values) == {"color": "أحمر", "size": "M"}
    assert parse_options("بدي الازرق مقاس لارج", values) == {"color": "أزرق", "size": "L"}
    assert parse_options("حمرا", values) == {"color": "أحمر"}
    assert parse_options("أخضر", values) is None             # not offered -> LLM decides
    assert parse_options("بس الجودة سيئة", values) is None  # extra words -> LLM (may be a complaint)


# --- confirm (task 1.9) ------------------------------------------------------------------

async def reach_summary(chat):
    r = await chat.say("بدي شنطة لنابلس 0599123456")
    assert "الإجمالي: 100.00 ILS" in r["content"]


SCRIPT = {"بدي شنطة لنابلس 0599123456": analysis(items=[item("شنطة")], address_text="نابلس", phone="0599123456")}


@pytest.mark.db
async def test_price_change_after_summary_shows_new_summary(db_tx):
    store = await new_store(db_tx)
    async with client_for(db_tx, llm=ScriptedLLM(SCRIPT)) as c:
        chat = Chat(c, store)
        await reach_summary(chat)
        await db_tx.execute("update products set price = 95 where store_id = $1 and name = 'حقيبة جلد'", store)
        r = await chat.say("تأكيد")
        assert "الأسعار أو العروض تغيّرت" in r["content"] and "الإجمالي: 105.00 ILS" in r["content"]
        assert await db_tx.fetchval("select count(*) from orders where store_id = $1", store) == 0
        r = await chat.say("تأكيد")                       # confirms the new total
        assert "رقم الطلب: #1" in r["content"] and "105.00" in r["content"]


@pytest.mark.db
async def test_stock_gone_between_summary_and_confirm(db_tx):
    store = await new_store(db_tx)
    async with client_for(db_tx, llm=ScriptedLLM(SCRIPT)) as c:
        chat = Chat(c, store)
        await reach_summary(chat)
        await db_tx.execute(
            "update product_variants set stock_quantity = 0 where store_id = $1 and product_id = "
            "(select id from products where store_id = $1 and name = 'حقيبة جلد')", store)
        r = await chat.say("تأكيد")
    assert "خلص هلأ من المخزون" in r["content"]
    assert await db_tx.fetchval("select count(*) from orders where store_id = $1", store) == 0


@pytest.mark.db
async def test_less_stock_at_confirm_offers_what_is_left(db_tx):
    store = await new_store(db_tx)
    script = {"بدي 3 شنط لنابلس 0599123456": analysis(items=[item("شنطة", qty=3)], address_text="نابلس", phone="0599123456")}
    async with client_for(db_tx, llm=ScriptedLLM(script)) as c:
        chat = Chat(c, store)
        r = await chat.say("بدي 3 شنط لنابلس 0599123456")
        assert "الإجمالي: 280.00 ILS" in r["content"]      # 3*90 + 10
        await db_tx.execute(
            "update product_variants set stock_quantity = 2 where store_id = $1 and product_id = "
            "(select id from products where store_id = $1 and name = 'حقيبة جلد')", store)
        r = await chat.say("تأكيد")
        assert "غير 2" in r["content"] and [b["id"] for b in r["buttons"]] == ["yes", "no"]
        r = await chat.say("نعم")
        assert "الإجمالي: 190.00 ILS" in r["content"]      # 2*90 + 10, fresh summary
        r = await chat.say("تأكيد")
        assert "رقم الطلب: #1" in r["content"]
