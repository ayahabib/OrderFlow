"""Match an extracted order item to a product and variant (ARCHITECTURE.md 5, step 2). No LLM.

1. Candidates: pg_trgm similarity of the normalized text against the product name
   (search_text) and its aliases (alias_text, a normalized generated column).
2. Head-word check: the item's first word (the noun) must really match a word of
   the name or an alias. This stops "طقم رياضي" matching "حذاء رياضي" through "رياضي".
3. Variant: filter the product's active variants by the given color/size.
   One left -> matched. Several -> ask (never guess, hard rule 4). None -> not available.
"""

import json
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Literal
from uuid import UUID

import asyncpg

from app.utils.arabic import normalize_arabic

Status = Literal["matched", "needs_variant", "variant_unavailable", "ambiguous_product", "not_found"]

MIN_SCORE = 0.3        # trigram score to be a candidate at all
HEAD_WORD_RATIO = 0.75  # how close the item's noun must be to a word of the name/alias
AMBIGUITY_GAP = 0.1    # two candidates closer than this -> ask which one
OPTION_LABELS = {"color": "اللون", "size": "المقاس"}


@dataclass
class MatchResult:
    status: Status
    product_id: UUID | None = None
    product_name: str | None = None
    variant_id: UUID | None = None
    variant_name: str | None = None
    quantity: int = 1
    stock_quantity: int | None = None
    stock_ok: bool | None = None  # informational; the real check is the atomic update at confirm (1.9)
    missing_options: list[str] = field(default_factory=list)          # needs_variant: what to ask
    option_values: dict[str, list[str]] = field(default_factory=dict)  # what the customer can choose
    candidates: list[str] = field(default_factory=list)                # ambiguous_product
    suggestions: list[str] = field(default_factory=list)               # not_found: closest products


def _norm_option(key: str, value: str | None) -> str | None:
    if not value:
        return None
    text = normalize_arabic(value)
    return text.upper() if key == "size" else text


def _words(text: str) -> list[str]:
    return [w for w in text.replace("|", " ").split() if w]


def _head_word_matches(query: str, search_text: str, alias_text: str) -> bool:
    """True if the query's first word is (nearly) a word of the name, or the query equals an alias."""
    head = query.split()[0]
    aliases = [a.strip() for a in alias_text.split("|") if a.strip()]
    if query in aliases:
        return True
    for word in _words(search_text) + _words(alias_text):
        if word == head or SequenceMatcher(None, head, word).ratio() >= HEAD_WORD_RATIO:
            return True
    return False


async def _candidates(conn: asyncpg.Connection, store_id: UUID, query: str) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        select id, name, search_text, coalesce(alias_text, '') as alias_text,
               greatest(similarity(search_text, $2),
                        word_similarity($2, search_text),
                        word_similarity($2, coalesce(alias_text, ''))) as score
          from products
         where store_id = $1 and is_active
         order by score desc, name
         limit 8
        """,
        store_id, query,
    )


async def _variants(conn: asyncpg.Connection, store_id: UUID, product_id: UUID) -> list[dict]:
    rows = await conn.fetch(
        """
        select id, name, options, stock_quantity from product_variants
         where product_id = $1 and store_id = $2 and is_active
         order by name
        """,
        product_id, store_id,
    )
    variants = []
    for r in rows:
        v = dict(r)
        if isinstance(v["options"], str):
            v["options"] = json.loads(v["options"])
        variants.append(v)
    return variants


async def active_variants(conn: asyncpg.Connection, store_id: UUID, product_id: UUID) -> list[dict]:
    """A product's active variants with options and stock (used by the owner stock chat)."""
    return await _variants(conn, store_id, product_id)


def pick_variant(variants: list[dict], color: str | None, size: str | None) -> tuple[Status, list[dict], list[str]]:
    """Filter variants by the given options. Returns (status, remaining variants, options still to ask)."""
    if not variants:
        return "variant_unavailable", [], []
    wanted = {k: _norm_option(k, v) for k, v in (("color", color), ("size", size)) if v}
    remaining = variants
    for key, value in wanted.items():
        if not any(key in v["options"] for v in remaining):
            continue  # the product doesn't vary by this option (e.g. a color for a one-color bag)
        remaining = [v for v in remaining if _norm_option(key, v["options"].get(key)) == value]
    if not remaining:
        return "variant_unavailable", [], []
    if len(remaining) == 1:
        return "matched", remaining, []
    differing = [k for k in ("color", "size")
                 if len({_norm_option(k, v["options"].get(k)) for v in remaining}) > 1]
    return "needs_variant", remaining, differing or ["color", "size"]


def _option_values(variants: list[dict], keys: list[str]) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for key in keys:
        seen: list[str] = []
        for v in variants:
            value = v["options"].get(key)
            if value and value not in seen:
                seen.append(value)
        values[key] = seen
    return values


async def match_item(
    conn: asyncpg.Connection, store_id: UUID, product_text: str,
    color: str | None = None, size: str | None = None, quantity: int = 1,
) -> MatchResult:
    query = normalize_arabic(product_text)
    if not query:
        return MatchResult(status="not_found", quantity=quantity)

    rows = await _candidates(conn, store_id, query)
    accepted = [r for r in rows if r["score"] >= MIN_SCORE and _head_word_matches(query, r["search_text"] or "", r["alias_text"])]
    if not accepted:
        suggestions = [r["name"] for r in rows if r["score"] >= 0.2][:3]
        return MatchResult(status="not_found", quantity=quantity, suggestions=suggestions)

    top = accepted[0]
    close = [r for r in accepted[1:] if top["score"] - r["score"] < AMBIGUITY_GAP]
    if close:
        return MatchResult(status="ambiguous_product", quantity=quantity,
                           candidates=[top["name"]] + [r["name"] for r in close])

    base = MatchResult(status="matched", product_id=top["id"], product_name=top["name"], quantity=quantity)
    variants = await _variants(conn, store_id, top["id"])
    status, remaining, missing = pick_variant(variants, color, size)
    base.status = status

    if status == "matched":
        v = remaining[0]
        base.variant_id, base.variant_name = v["id"], v["name"]
        base.stock_quantity = v["stock_quantity"]
        base.stock_ok = v["stock_quantity"] >= quantity
    elif status == "needs_variant":
        base.missing_options = missing
        base.option_values = _option_values(remaining, missing)
    else:  # variant_unavailable: show what does exist
        keys = [k for k in ("color", "size") if any(k in v["options"] for v in variants)]
        base.option_values = _option_values(variants, keys)
    return base


def clarifying_question(result: MatchResult) -> str | None:
    """Short Arabic question for the customer, or None when nothing needs asking.

    Only names and options from the store data; never prices or invented info.
    """
    name = result.product_name
    if result.status == "needs_variant":
        parts = []
        for key in result.missing_options:
            values = "، ".join(result.option_values.get(key, []))
            parts.append(f"{OPTION_LABELS[key]} ({values})" if values else OPTION_LABELS[key])
        return f"أي {' و'.join(parts)} بدك من {name}؟"
    if result.status == "variant_unavailable":
        offer = "؛ ".join(f"{OPTION_LABELS[k]}: {'، '.join(v)}" for k, v in result.option_values.items() if v)
        return f"هاد الخيار مش متوفر من {name}." + (f" المتوفر: {offer}" if offer else "")
    if result.status == "ambiguous_product":
        return f"قصدك أي واحد: {' أو '.join(result.candidates)}؟"
    if result.status == "not_found":
        hint = f" عنا مثلاً: {'، '.join(result.suggestions)}." if result.suggestions else ""
        return "للأسف هاد المنتج مش موجود عنا." + hint
    if result.status == "matched" and result.stock_ok is False:
        available = result.stock_quantity or 0
        if available == 0:
            return f"{name} ({result.variant_name}) خلص من المخزون حالياً."
        return f"المتوفر من {name} ({result.variant_name}) {available} بس. بتحب تاخد {available}؟"
    return None
