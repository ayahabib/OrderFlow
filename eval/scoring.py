"""Scoring for the extraction eval (eval/README.md "كيف نقيس").

Text fields are compared after Arabic normalization. Items are paired with the
expected items by best product_text match (order in the message doesn't matter);
a missing item counts every field of it as wrong.
"""

from difflib import SequenceMatcher
from itertools import permutations

from app.utils.arabic import normalize_arabic

ITEM_FIELDS = ("product_text", "color", "size", "quantity")
TOP_FIELDS = ("address_text", "phone", "wants_to_confirm")


def norm(value):
    if value is None:
        return None
    if isinstance(value, (bool, int)):
        return value
    text = normalize_arabic(str(value))
    return text or None


def same(a, b) -> bool:
    return norm(a) == norm(b)


def pair_items(expected: list[dict], predicted: list[dict]) -> list[tuple[dict, dict | None]]:
    """Pair each expected item with at most one predicted item, maximizing name similarity."""
    if not expected:
        return []
    padded = predicted + [None] * max(0, len(expected) - len(predicted))

    def score(order) -> float:
        total = 0.0
        for exp, pred in zip(expected, order):
            if pred is not None:
                total += SequenceMatcher(None, norm(exp["product_text"]) or "", norm(pred["product_text"]) or "").ratio()
        return total

    best = max(permutations(padded, len(expected)), key=score)  # lists are tiny (<= a few items)
    return list(zip(expected, best))


def score_record(record: dict, prediction: dict) -> dict:
    """Per-field correctness for one test message. Fields are True/False, or absent when not scored."""
    result = {"intent": prediction["intent"] == record["expected_intent"]}
    expected = record["expected"]
    if expected is None:  # edit_order messages: intent only (README)
        return result

    pairs = pair_items(expected["items"], prediction["items"])
    for field in ITEM_FIELDS:
        result[field] = [pred is not None and same(exp[field], pred[field]) for exp, pred in pairs]
    result["item_count"] = len(expected["items"]) == len(prediction["items"])
    for field in TOP_FIELDS:
        result[field] = same(expected[field], prediction[field])
    result["full_message"] = (
        result["intent"] and result["item_count"]
        and all(all(result[f]) for f in ITEM_FIELDS)
        and all(result[f] for f in TOP_FIELDS)
    )
    return result


def summarize(scores: list[dict]) -> dict[str, dict]:
    """{field: {"correct": n, "total": m, "accuracy": n/m}}. Item fields count each expected item."""
    totals: dict[str, list[int]] = {}
    for s in scores:
        for field, value in s.items():
            values = value if isinstance(value, list) else [value]
            bucket = totals.setdefault(field, [0, 0])
            bucket[0] += sum(bool(v) for v in values)
            bucket[1] += len(values)
    return {
        f: {"correct": c, "total": t, "accuracy": round(c / t, 4) if t else None}
        for f, (c, t) in totals.items()
    }


# --- archive eval (task 3.7): bot turn vs. staff turn ------------------------------------

ARCHIVE_ACTIONS = ("ask", "summary", "order_placed", "answer", "handoff")


def bot_action(*, escalated: bool, order_placed: bool, mode: str, pending: bool, has_draft: bool) -> str:
    """What the bot did on one turn, from engine signals (not from its wording)."""
    if escalated:
        return "handoff"
    if order_placed:
        return "order_placed"
    if mode == "awaiting_confirmation" and not pending:
        return "summary"
    if pending or has_draft:
        return "ask"            # an open question, or a draft still missing info
    return "answer"             # Q&A, a decline, a cancel, a greeting


def _has(text: str, alternatives: list[str]) -> bool:
    return any(normalize_arabic(a) in text for a in alternatives)


def score_turn(expected: dict, action: str, reply: str) -> dict:
    """Pass = same action as the staff (or one of `any_of`), every `include` group present, no `exclude` group."""
    text = normalize_arabic(reply or "")
    allowed = expected.get("any_of") or [expected["action"]]
    missing = [g for g in expected.get("include", []) if not _has(text, g)]
    forbidden = [g for g in expected.get("exclude", []) if _has(text, g)]
    action_ok = action in allowed
    return {"action_ok": action_ok, "facts_ok": not missing and not forbidden,
            "missing": missing, "forbidden": forbidden, "passed": action_ok and not missing and not forbidden}
