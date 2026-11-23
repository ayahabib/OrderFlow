"""eval/scoring.py: per-field comparison, item pairing, summary."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval"))

from scoring import bot_action, pair_items, score_record, score_turn, summarize  # noqa: E402


def item(product, color=None, size=None, qty=1):
    return {"product_text": product, "color": color, "size": size, "quantity": qty, "notes": None}


def record(items, intent="order", address=None, phone=None, confirm=False):
    return {"expected_intent": intent,
            "expected": {"items": items, "address_text": address, "phone": phone, "wants_to_confirm": confirm}}


def prediction(items, intent="order", address=None, phone=None, confirm=False):
    return {"intent": intent, "sensitive_reason": None, "items": items,
            "address_text": address, "phone": phone, "wants_to_confirm": confirm}


def test_perfect_match_after_normalization():
    r = record([item("فستان", "أحمر", "M")], address="نابلس، شارع فيصل", phone="0599123456")
    p = prediction([item("فستان", "احمر", "M")], address="نابلس، شارع فيصل", phone="0599123456")
    s = score_record(r, p)
    assert s["full_message"] is True
    assert s["color"] == [True]  # "احمر" == "أحمر" after normalization


def test_items_paired_regardless_of_order():
    r = record([item("قميص", "أبيض", "M"), item("بنطلون جينز", "أزرق", "32")])
    p = prediction([item("بنطلون جينز", "أزرق", "32"), item("قميص", "أبيض", "M")])
    assert score_record(r, p)["full_message"] is True


def test_missing_item_counts_all_its_fields_wrong():
    r = record([item("قميص"), item("حذاء", "أسود", "42")])
    s = score_record(r, prediction([item("قميص")]))
    assert s["item_count"] is False
    assert s["product_text"] == [True, False] and s["quantity"] == [True, False]
    assert s["full_message"] is False


def test_wrong_field_is_isolated():
    s = score_record(record([item("فستان", "أحمر", "M", 2)]), prediction([item("فستان", "أحمر", "L", 2)]))
    assert (s["product_text"], s["size"], s["quantity"]) == ([True], [False], [True])
    assert s["full_message"] is False


def test_edit_order_records_score_intent_only():
    r = {"expected_intent": "edit_order", "expected": None}
    assert score_record(r, prediction([], intent="edit_order")) == {"intent": True}


def test_pairing_prefers_similar_names():
    pairs = pair_items([item("حذاء"), item("وشاح")], [item("وشاح"), item("حذاء رياضي")])
    assert [(e["product_text"], p["product_text"]) for e, p in pairs] == [("حذاء", "حذاء رياضي"), ("وشاح", "وشاح")]


def test_summarize_counts_each_item():
    scores = [
        {"intent": True, "product_text": [True, False], "full_message": False},
        {"intent": False, "product_text": [True], "full_message": True},
    ]
    s = summarize(scores)
    assert s["intent"] == {"correct": 1, "total": 2, "accuracy": 0.5}
    assert s["product_text"] == {"correct": 2, "total": 3, "accuracy": 0.6667}


# --- archive eval (3.7) ---

def test_bot_action_from_engine_signals():
    base = dict(escalated=False, order_placed=False, mode="idle", pending=False, has_draft=False)
    assert bot_action(**{**base, "escalated": True, "has_draft": True}) == "handoff"
    assert bot_action(**{**base, "order_placed": True}) == "order_placed"
    assert bot_action(**{**base, "mode": "awaiting_confirmation", "has_draft": True}) == "summary"
    assert bot_action(**{**base, "mode": "awaiting_confirmation", "pending": True}) == "ask"   # e.g. "same order again?"
    assert bot_action(**{**base, "mode": "collecting", "has_draft": True}) == "ask"
    assert bot_action(**base) == "answer"


def test_score_turn_action_include_exclude():
    exp = {"action": "summary", "include": [["130"], ["توصيل", "التوصيل"]]}
    assert score_turn(exp, "summary", "الإجمالي: 130.00 ILS، التوصيل 10")["passed"]
    missing = score_turn(exp, "summary", "الإجمالي: 120.00 ILS")
    assert not missing["passed"] and missing["action_ok"] and len(missing["missing"]) == 2
    assert not score_turn(exp, "ask", "الإجمالي 130 والتوصيل")["action_ok"]

    out_of_zone = {"any_of": ["ask", "answer"], "include": [["نابلس"]], "exclude": [["25"]]}
    assert score_turn(out_of_zone, "answer", "منوصّل بس على: نابلس، جنين")["passed"]
    invented = score_turn(out_of_zone, "answer", "التوصيل 25 لنابلس")
    assert not invented["passed"] and invented["forbidden"] == [["25"]]
    assert score_turn({"action": "handoff"}, "handoff", "حوّلت سؤالك")["passed"]
