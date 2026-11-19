"""eval/scoring.py: per-field comparison, item pairing, summary."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval"))

from scoring import pair_items, score_record, summarize  # noqa: E402


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
