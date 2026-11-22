"""Customer questions (task 2.2, ARCHITECTURE.md section 6). Forced-escalation rules ran before this.

Three paths, in order:
1. Product questions (the LLM found product names): price after offers, in stock or not,
   colors/sizes. Computed in code from SQL (hard rule 1). Nothing is added to the order.
2. Delivery questions: zones and fees from the delivery_zones table, in code.
3. Everything else: RAG over the store's knowledge base. Low similarity, NOT_FOUND, or an
   answer containing a number the passages don't have -> escalate to the owner (hard rule 4).
If the bot was waiting for an answer (size, address...), the reply ends by asking it again.
"""

import json
import re
from datetime import datetime, timezone
from decimal import Decimal

from app.channels.base import OutboundMessage
from app.db import knowledge as kb_db
from app.llm.client import LLMError
from app.llm.qa import answer_from_passages
from app.llm.schemas import ExtractedItem
from app.services import delivery, pricing
from app.services.product_matching import OPTION_LABELS, clarifying_question, match_item
from app.utils.arabic import normalize_arabic, to_latin_digits

# Below this the passages are about something else. Kept low on purpose: the LLM's NOT_FOUND is
# the real gate (eval/run_rag_eval.py: every unanswerable question that reached it was refused),
# while 0.3 cut a correct returns question at 0.24.
MIN_SIMILARITY = 0.2
TOP_K = 3
MAX_PRODUCTS = 3       # product questions answered per message
ORDER_HINT = "إذا بدك تطلب، اكتبلي المنتج واللون والمقاس والكمية."

# Normalized words that make a message a delivery question.
_DELIVERY = re.compile(r"توصيل|توصل|بتوصل|بتوصلو|توصلو|بيوصل|شحن|دليفري|delivery")


def is_delivery_question(text: str) -> bool:
    return bool(_DELIVERY.search(normalize_arabic(text)))


def numbers_in(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:[.,]\d+)?", to_latin_digits(text)))


def grounded(answer: str, passages: list[str]) -> bool:
    """Every number in the answer appears in the passages (no invented hours, prices, days)."""
    allowed = set().union(*(numbers_in(p) for p in passages)) if passages else set()
    return numbers_in(answer) <= allowed


def _money(value: Decimal, currency: str) -> str:
    return f"{pricing.money(value)} {currency}"


# --- 1. product questions ---------------------------------------------------------------

async def _variant_prices(ctx, product_id, offers, now) -> list[tuple[dict, pricing.Price]]:
    rows = await ctx.conn.fetch(
        """
        select v.id, v.name, v.options, v.stock_quantity, coalesce(v.price_override, p.price) as base
          from product_variants v join products p on p.id = v.product_id and p.store_id = v.store_id
         where v.product_id = $1 and v.store_id = $2 and v.is_active and p.is_active
        """,
        product_id, ctx.store_id,
    )
    out = []
    for r in rows:
        v = dict(r)
        if isinstance(v["options"], str):  # jsonb arrives as text (no codec)
            v["options"] = json.loads(v["options"])
        out.append((v, pricing.variant_price(r["base"], product_id, r["id"], offers, now)))
    return out


def _price_text(prices: list[pricing.Price], currency: str) -> str:
    low = min(p.unit_price for p in prices)
    high = max(p.unit_price for p in prices)
    text = _money(low, currency) if low == high else f"من {_money(low, currency)} لـ {_money(high, currency)}"
    if any(p.offer for p in prices):
        text += " (مع العرض)"
    return text


async def _describe_product(ctx, item: ExtractedItem, offers, now) -> str:
    result = await match_item(ctx.conn, ctx.store_id, item.product_text, item.color, item.size, 1)
    if result.status in ("not_found", "ambiguous_product"):
        return clarifying_question(result) or "ما لقيت هالمنتج."
    variants = await _variant_prices(ctx, result.product_id, offers, now)
    if result.status == "matched":
        row, price = next((v, p) for v, p in variants if v["id"] == result.variant_id)
        stock = "متوفر" if row["stock_quantity"] > 0 else "خلصان حالياً"
        label = result.product_name if result.variant_name == "الافتراضي" else f"{result.product_name} ({result.variant_name})"
        return f"{label}: {_price_text([price], ctx.currency)}، {stock}."
    in_stock = [(v, p) for v, p in variants if v["stock_quantity"] > 0]
    if not in_stock:
        return f"{result.product_name} خلصان حالياً."
    text = f"{result.product_name}: {_price_text([p for _, p in in_stock], ctx.currency)}."
    keys = [k for k in ("color", "size") if any(k in (v["options"] or {}) for v, _ in in_stock)]
    for key in keys:
        values = sorted({v["options"][key] for v, _ in in_stock if key in (v["options"] or {})})
        text += f" {OPTION_LABELS[key]} المتوفر: {'، '.join(values)}."
    if result.status == "variant_unavailable":
        text = "الخيار الي سألت عنه مش متوفر. " + text
    return text


async def _product_answer(ctx, items: list[ExtractedItem]) -> str:
    offers = await pricing.load_offers(ctx.conn, ctx.store_id)
    now = datetime.now(timezone.utc)
    lines = [await _describe_product(ctx, item, offers, now) for item in items[:MAX_PRODUCTS]]
    return "\n".join(lines + [ORDER_HINT])


# --- 2. delivery questions --------------------------------------------------------------

async def _delivery_answer(ctx, text: str) -> str | None:
    """None when the store has no zones at all (setup problem: the owner must answer)."""
    zones = await delivery.active_zones(ctx.conn, ctx.store_id)
    if not zones:
        return None
    offers = await pricing.load_offers(ctx.conn, ctx.store_id)
    now = datetime.now(timezone.utc)
    free = min((o.min_order_total for o in offers if o.kind == "free_delivery" and pricing.is_live(o, now)), default=None)
    free_note = f" والتوصيل مجاني للطلبات من {_money(free, ctx.currency)} وفوق." if free is not None else ""

    zone = delivery.find_zone(text, zones)
    if zone is not None:
        return f"اه منوصّل على {zone.name}، وأجرة التوصيل {_money(zone.fee, ctx.currency)}.{free_note}"
    listing = "، ".join(f"{z.name} ({_money(z.fee, ctx.currency)})" for z in zones)
    return f"منوصّل على: {listing}.{free_note}"


# --- 3. store info (RAG) ----------------------------------------------------------------

async def _knowledge_answer(ctx, text: str) -> str | None:
    """The answer, or None to escalate. Raises LLMError when the embedding or LLM call fails."""
    if ctx.embedder is None or ctx.llm is None:
        raise LLMError("not_configured", "embeddings or LLM not configured")
    [vector] = await ctx.embedder.embed([text])
    hits = await kb_db.match(ctx.conn, ctx.store_id, vector, TOP_K)
    if not hits or hits[0]["similarity"] < MIN_SIMILARITY:
        return None
    passages = [f"{h['question']}\n{h['answer']}" if h["question"] else h["answer"] for h in hits]
    result = await answer_from_passages(ctx.llm, text, passages)
    if not result.found or not grounded(result.answer, passages):
        return None
    return result.answer


# --- entry point --------------------------------------------------------------------------

async def answer(ctx, text: str, items: list[ExtractedItem]) -> tuple[OutboundMessage | None, str | None]:
    """(reply, None) when answered, or (None, rule) to escalate with that rule."""
    if items:
        return OutboundMessage(text=await _product_answer(ctx, items)), None
    if is_delivery_question(text):
        reply = await _delivery_answer(ctx, text)
        return (OutboundMessage(text=reply), None) if reply else (None, "unknown_zone")
    try:
        reply = await _knowledge_answer(ctx, text)
    except LLMError:
        return None, "llm_failure"   # hard rule 6: the owner answers instead
    return (OutboundMessage(text=reply), None) if reply else (None, "not_in_data")
