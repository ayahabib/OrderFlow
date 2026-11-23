"""Order flow state machine (ARCHITECTURE.md section 5). State lives in conversations.bot_state.

One customer message -> `handle` -> (reply, new state). Order of work:
0. Forced-escalation keywords (engine/rules.py), before anything else and without the LLM.
1. Cheap deterministic steps first (buttons, and short answers to the bot's open question:
   a size/color from the offered list, a phone number, a zone name, yes/no).
2. Otherwise one LLM call (intent + extraction), with the bot's last question as context.
3. Hard-rule escalations: LLM failure, sensitive reason, large order (code), no delivery
   zones set up. Questions are answered from store data (engine/qa.py) or escalated. An area outside the store's zones is declined, not escalated.
4. `_advance`: resolve queued items (matching), then address -> zone -> phone -> summary.

The LLM never produces numbers: prices, fees and totals come from services/order_totals.py.
"""

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal
from uuid import UUID

import asyncpg
from pydantic import BaseModel, Field

from app.channels.base import Button, OutboundMessage
from app.llm.client import LLM, LLMError
from app.llm.embeddings import Embedder
from app.llm.edits import extract_edits
from app.llm.extraction import analyze_message, clean_size
from app.engine import qa, rules
from app.services import delivery, escalation, order_totals, orders
from app.services import product_matching
from app.services.product_matching import OPTION_LABELS, clarifying_question, match_item
from app.utils.arabic import normalize_arabic, to_latin_digits

CONFIRM_BUTTONS = [Button(id="confirm", title="تأكيد"), Button(id="edit", title="تعديل"), Button(id="cancel", title="إلغاء")]
YES_NO_BUTTONS = [Button(id="yes", title="نعم"), Button(id="no", title="لا")]

WELCOME = "أهلاً فيك! شو بتحب تطلب؟ اكتبلي اسم المنتج واللون والمقاس."
ASK_ADDRESS = "لوين بدك التوصيل؟ اكتبلي المدينة والعنوان."
ASK_PHONE = "شو رقم تلفونك للتواصل؟"


# --- state ---------------------------------------------------------------------

class DraftItem(BaseModel):
    variant_id: UUID
    product_id: UUID
    product_name: str
    variant_name: str
    quantity: int
    notes: str | None = None


class QueuedItem(BaseModel):
    """An item the customer asked for that isn't resolved to a variant yet."""
    product_text: str
    color: str | None = None
    size: str | None = None
    quantity: int = 1
    notes: str | None = None


class Pending(BaseModel):
    """The one question the bot is waiting on."""
    kind: Literal["variant", "product", "stock", "address", "zone", "phone", "duplicate"]
    question: str
    item: QueuedItem | None = None
    missing: list[str] = Field(default_factory=list)              # variant: options to ask
    option_values: dict[str, list[str]] = Field(default_factory=dict)
    candidates: list[str] = Field(default_factory=list)            # product: which one
    stock_item: DraftItem | None = None                            # stock: offer to take `available`
    available: int | None = None
    duplicate_of: UUID | None = None                               # duplicate: the earlier order (3.5)


class BotState(BaseModel):
    mode: Literal["idle", "collecting", "awaiting_confirmation"] = "idle"
    items: list[DraftItem] = Field(default_factory=list)
    queue: list[QueuedItem] = Field(default_factory=list)
    pending: Pending | None = None
    address: str | None = None
    zone_id: UUID | None = None
    zone_name: str | None = None
    phone: str | None = None
    # Total shown in the last summary (computed by code). Confirm re-computes and, if it
    # differs (price/offer changed meanwhile), shows a fresh summary instead of ordering.
    quoted_total: Decimal | None = None
    # The customer said this is a new order although it matches this earlier one (3.5).
    duplicate_ok_for: UUID | None = None
    # The one product the last question was about, with its in-stock options, so a reply like
    # "ازرق 32" right after the answer starts an order for it (archive eval 3.7).
    asked_product: str | None = None
    asked_options: dict[str, list[str]] = Field(default_factory=dict)

    @classmethod
    def load(cls, raw) -> "BotState":
        try:
            return cls.model_validate(raw or {})
        except ValueError:
            return cls()  # old/unknown shape: start fresh rather than crash


@dataclass
class Context:
    conn: asyncpg.Connection
    store_id: UUID
    conversation_id: UUID
    customer_id: UUID
    llm: LLM | None
    currency: str
    large_item_qty: int
    large_total_qty: int
    large_amount: Decimal
    customer_phone: str | None = None
    embedder: Embedder | None = None   # knowledge-base search for questions (2.2)
    tracking_base_url: str = ""        # frontend address for /track/<token> links (3.6)


@dataclass
class Outcome:
    reply: OutboundMessage
    state: BotState
    escalated: bool = False
    notes: list[str] = field(default_factory=list)


# --- deterministic parsing of short answers -------------------------------------------

_BUTTON_WORDS = {"تاكيد": "confirm", "اكد": "confirm", "confirm": "confirm",
                 "تعديل": "edit", "edit": "edit", "الغاء": "cancel", "cancel": "cancel"}
_YES = {"نعم", "اه", "ايوه", "ايوا", "اكيد", "تمام", "ماشي", "موافق", "yes", "ok", "اوكي"}
_NO = {"لا", "no", "بلاش", "مش"}
_FILLER = {"خلص", "طيب", "بدي", "بدنا", "مقاس", "المقاس", "لون", "اللون", "و", "بس", "يعني", "خليه", "خليها", "حجم", "الحجم", "please"}
# Feminine/colloquial colors -> the normalized masculine form used in options.
_COLOR_FORMS = {"حمرا": "احمر", "حمراء": "احمر", "زرقا": "ازرق", "زرقاء": "ازرق", "سودا": "اسود",
                "سوداء": "اسود", "بيضا": "ابيض", "بيضاء": "ابيض", "خضرا": "اخضر", "خضراء": "اخضر"}
_PHONE = re.compile(r"^\+?\d{9,15}$")


def _tokens(text: str) -> list[str]:
    return normalize_arabic(text).replace("،", " ").replace(",", " ").replace(".", " ").split()


def button_action(text: str) -> str | None:
    words = _tokens(text)
    return _BUTTON_WORDS.get(words[0]) if len(words) == 1 else None


def yes_no(text: str) -> bool | None:
    words = _tokens(text)
    if not words or len(words) > 3:
        return None
    if any(w in _NO for w in words):
        return False
    if any(w in _YES for w in words):
        return True
    return None


def parse_phone(text: str) -> str | None:
    compact = re.sub(r"[\s\-()]", "", to_latin_digits(text.strip()))
    return compact if _PHONE.fullmatch(compact) else None


def parse_options(text: str, option_values: dict[str, list[str]]) -> dict[str, str] | None:
    """Every word must be filler or one of the offered values; else None (let the LLM read it)."""
    wanted: dict[str, str] = {}
    lookup = {}
    for key, values in option_values.items():
        for value in values:
            lookup[(key, normalize_arabic(value).upper() if key == "size" else normalize_arabic(value))] = value
    for word in _tokens(text):
        if word in _FILLER:
            continue
        bare = word[2:] if word.startswith("ال") and len(word) > 3 else word
        bare = _COLOR_FORMS.get(bare, bare)
        size = (clean_size(word) or "").upper()
        hit = next((v for (k, n), v in lookup.items()
                    if (k == "color" and n == bare) or (k == "size" and n == size)), None)
        if hit is None:
            return None
        key = next(k for (k, _), v in lookup.items() if v == hit)
        wanted[key] = hit
    return wanted or None


# --- helpers ---------------------------------------------------------------------

def _money(value: Decimal, currency: str) -> str:
    return f"{value:.2f} {currency}"


def _label(item: DraftItem) -> str:
    return item.product_name if item.variant_name == "الافتراضي" else f"{item.product_name} ({item.variant_name})"


def _merge(items: list[DraftItem], new: DraftItem) -> None:
    for existing in items:
        if existing.variant_id == new.variant_id:
            existing.quantity += new.quantity
            existing.notes = existing.notes or new.notes
            return
    items.append(new)


def _reply(text: str, buttons: list[Button] | None = None) -> OutboundMessage:
    return OutboundMessage(text=text, buttons=buttons or [])


async def _escalate(ctx: Context, state: BotState, question: str, rule: escalation.Rule) -> Outcome:
    text = await escalation.escalate(ctx.conn, ctx.store_id, ctx.conversation_id, question, rule)
    state.pending = None
    return Outcome(reply=_reply(text), state=state, escalated=True)


def _reset() -> BotState:
    return BotState()


STATUS_FOR_CUSTOMER = {
    "new": "استلمناه وبيتجهّز قريباً",
    "in_progress": "قيد التجهيز والتوصيل",
    "delivered": "تم التوصيل",
    "cancelled": "ملغي",
}
STATUS_DAYS = 30   # orders older than this aren't "where is my order?" material
STATUS_MAX = 3


def _tracking_link(ctx: Context, token: str) -> str:
    return f"{ctx.tracking_base_url.rstrip('/')}/track/{token}"


async def _order_status(ctx: Context, state: BotState, text: str) -> Outcome:
    """"Where is my order?": the status comes from the database, never from the LLM (owner's choice, 3.6).

    No recent order for this customer -> the owner answers (hard rule 4: never guess).
    The draft (if any) is kept, so the customer can carry on ordering.
    """
    rows = await ctx.conn.fetch(
        """
        select order_number, status::text as status, tracking_token
          from orders
         where store_id = $1 and customer_id = $2 and created_at >= now() - make_interval(days => $3)
         order by created_at desc
         limit $4
        """,
        ctx.store_id, ctx.customer_id, STATUS_DAYS, STATUS_MAX,
    )
    if not rows:
        return await _escalate(ctx, state, text, "order_status")
    lines = [f"طلبك #{r['order_number']}: {STATUS_FOR_CUSTOMER[r['status']]}.\n"
             f"تابعه هون: {_tracking_link(ctx, r['tracking_token'])}" for r in rows]
    lines.append("إذا في مشكلة بالطلب احكيلي وبحوّلك لصاحب المتجر.")
    return Outcome(_reply("\n\n".join(lines)), state)


async def _decline_area(ctx: Context) -> Outcome:
    """Out of our delivery area: say so, list where we deliver, close the order. No owner handoff (owner's choice)."""
    names = "، ".join(z.name for z in await delivery.active_zones(ctx.conn, ctx.store_id))
    text = (f"للأسف ما منوصّل على هالمنطقة. منوصّل بس على: {names}.\n"
            "إذا عندك عنوان بمنطقة منهم، ابعتلي طلبك من جديد.")
    return Outcome(_reply(text), _reset())


# --- main entry -------------------------------------------------------------------

async def handle(ctx: Context, state: BotState, text: str) -> Outcome:
    notes: list[str] = []
    asked, state.asked_product = state.asked_product, None   # good for the very next message only

    # 0. Hard rule 5: complaint, discount, allergy, "talk to a person"... the bot never continues alone.
    rule = rules.keyword_rule(text)
    if rule == "order_status":
        return await _order_status(ctx, state, text)   # answered from the database (3.6)
    if rule:
        return await _escalate(ctx, state, text, rule)

    # 1. Buttons (exact words only).
    action = button_action(text)
    if action == "cancel":
        if state.items or state.queue or state.pending:
            return Outcome(_reply("تم إلغاء الطلب. إذا بدك تطلب إشي تاني أنا جاهز."), _reset())
        return Outcome(_reply("ما في طلب مفتوح حالياً. شو بتحب تطلب؟"), state)
    if state.mode == "awaiting_confirmation" and action == "confirm":
        return await _confirm(ctx, state, notes)
    if state.mode == "awaiting_confirmation" and action == "edit":
        state.mode = "collecting"
        state.pending = None
        return Outcome(_reply("شو بدك تعدّل؟ اكتبلي التعديل (مثلاً: زيد قطعة، أو غيّر العنوان)."), state)

    # 2. Short answers to the open question, without the LLM.
    handled = await _answer_pending_deterministically(ctx, state, text, notes)
    if handled is not None:
        return handled
    if asked and not state.pending:
        picked = parse_options(text, state.asked_options)
        if picked:
            state.queue.append(QueuedItem(product_text=asked, **picked))
            return await _advance(ctx, state, notes)

    # 3. LLM.
    if ctx.llm is None:
        return await _escalate(ctx, state, text, "llm_failure")
    try:
        analysis = await analyze_message(ctx.llm, text, context=state.pending.question if state.pending else None)
    except LLMError:
        return await _escalate(ctx, state, text, "llm_failure")  # hard rule 6: never lose the message

    if analysis.sensitive_reason == "order_status":
        return await _order_status(ctx, state, text)
    if analysis.sensitive_reason:
        return await _escalate(ctx, state, text, analysis.sensitive_reason)

    intent = analysis.intent
    if intent == "cancel":
        return Outcome(_reply("تم إلغاء الطلب. إذا بدك تطلب إشي تاني أنا جاهز."), _reset())
    if intent == "confirm" or analysis.wants_to_confirm:
        if state.mode == "awaiting_confirmation":
            return await _confirm(ctx, state, notes)
        if state.pending and state.pending.kind == "stock":
            return await _answer_stock(ctx, state, True, notes)
    if intent == "question":
        return await _answer_question(ctx, state, text, analysis)
    if intent == "edit_order" and state.items:
        return await _edit_order(ctx, state, text, notes)

    changed = _apply_analysis(state, analysis, text)
    if state.pending and state.pending.kind == "zone":
        # We listed our zones and the reply names none of them: we don't deliver there.
        return await _decline_area(ctx)
    if not changed:
        if intent == "other" and not (state.items or state.queue or state.pending):
            return Outcome(_reply(WELCOME), state)
        if state.pending:
            return Outcome(_reply(state.pending.question, _pending_buttons(state.pending)), state)
        if state.mode == "awaiting_confirmation":
            return await _summary(ctx, state, notes)
        return Outcome(_reply(WELCOME), state)

    if state.mode == "awaiting_confirmation":
        state.mode = "collecting"  # something changed after the summary: show a fresh one
    return await _advance(ctx, state, notes)


EDIT_UNCLEAR = "شو بدك تعدّل بالظبط؟ طلبك هلق:"


async def _variant_options(ctx: Context, variant_id: UUID) -> dict:
    raw = await ctx.conn.fetchval(
        "select options from product_variants where id = $1 and store_id = $2", variant_id, ctx.store_id)
    return (json.loads(raw) if isinstance(raw, str) else raw) or {}


async def _edit_order(ctx: Context, state: BotState, text: str, notes: list[str]) -> Outcome:
    """Change the draft before confirming (2.6): the LLM names the operations, code applies them."""
    try:
        edits = await extract_edits(ctx.llm, text, [f"{_label(i)} × {i.quantity}" for i in state.items])
    except LLMError:
        return await _escalate(ctx, state, text, "llm_failure")

    changed = False
    removed: set[int] = set()
    for op in edits.ops:
        if op.op == "add_item":
            if op.product_text:
                state.queue.append(QueuedItem(product_text=op.product_text, color=op.color, size=clean_size(op.size),
                                              quantity=max(1, op.quantity or 1)))
                changed = True
            continue
        if op.line is None or not 1 <= op.line <= len(state.items) or op.line - 1 in removed:
            continue  # a line that doesn't exist: ignore rather than guess
        item = state.items[op.line - 1]
        if op.op == "remove":
            removed.add(op.line - 1)
        elif op.op == "set_quantity" and op.quantity and op.quantity > 0:
            item.quantity = op.quantity
        elif op.op == "add_quantity" and op.quantity and op.quantity > 0:
            item.quantity += op.quantity
        elif op.op == "change_options" and (op.color or op.size):
            # Same product, new variant: keep what didn't change, re-match (stock, price) like a new item.
            current = await _variant_options(ctx, item.variant_id)
            state.queue.append(QueuedItem(
                product_text=item.product_name, color=op.color or current.get("color"),
                size=clean_size(op.size) or current.get("size"), quantity=item.quantity, notes=item.notes))
            removed.add(op.line - 1)
        else:
            continue
        changed = True

    if not changed:
        listing = "\n".join(f"- {_label(i)} × {i.quantity}" for i in state.items)
        return Outcome(_reply(f"{EDIT_UNCLEAR}\n{listing}"), state)
    state.items = [i for n, i in enumerate(state.items) if n not in removed]
    state.mode, state.pending, state.quoted_total = "collecting", None, None
    if not state.items and not state.queue:
        return Outcome(_reply("شلت كل شي من الطلب. إذا بدك تطلب إشي تاني أنا جاهز."), _reset())
    return await _advance(ctx, state, notes)


async def _answer_question(ctx: Context, state: BotState, text: str, analysis) -> Outcome:
    """Answer from store data (engine/qa.py) without touching the order, or hand over to the owner."""
    reply, rule = await qa.answer(ctx, text, analysis.items)
    if rule:
        return await _escalate(ctx, state, text, rule)
    if len(analysis.items) == 1 and not state.pending:
        await _remember_asked_product(ctx, state, analysis.items[0])
    # Keep an order in progress going: repeat what we were waiting for.
    if state.pending:
        return Outcome(_reply(f"{reply.text}\n\n{state.pending.question}", _pending_buttons(state.pending)), state)
    if state.mode == "awaiting_confirmation":
        return Outcome(_reply(f"{reply.text}\n\nطلبك جاهز، بدك أأكده؟", CONFIRM_BUTTONS), state)
    return Outcome(reply, state)


async def _remember_asked_product(ctx: Context, state: BotState, item) -> None:
    """After answering about one product, keep it (and its in-stock options) for the next message."""
    m = await match_item(ctx.conn, ctx.store_id, item.product_text, item.color, item.size, 1)
    if m.product_id and m.status in ("matched", "needs_variant", "variant_unavailable"):
        options = await product_matching.in_stock_options(ctx.conn, ctx.store_id, m.product_id)
        if options:
            state.asked_product, state.asked_options = m.product_name, options


def _pending_buttons(pending: Pending) -> list[Button]:
    return YES_NO_BUTTONS if pending.kind in ("stock", "duplicate") else []


async def _answer_pending_deterministically(ctx: Context, state: BotState, text: str, notes: list[str]) -> Outcome | None:
    p = state.pending
    if p is None:
        return None
    if p.kind == "phone":
        phone = parse_phone(text)
        if phone:
            state.phone, state.pending = phone, None
            return await _advance(ctx, state, notes)
    elif p.kind == "stock":
        answer = yes_no(text)
        if answer is not None:
            return await _answer_stock(ctx, state, answer, notes)
    elif p.kind == "duplicate":
        answer = yes_no(text)
        if answer is None and button_action(text) == "confirm":
            answer = True
        if answer is True:
            state.pending, state.duplicate_ok_for = None, p.duplicate_of
            return await _confirm(ctx, state, notes)
        if answer is False:
            return Outcome(_reply("تمام، ما سجّلنا طلب جديد. طلبك الأول لسّا قائم. إذا بدك إشي تاني أنا جاهز."),
                           _reset())
        state.pending = None  # something else (an edit, a question): carry on normally
    elif p.kind == "zone":
        zone = delivery.find_zone(text, await delivery.active_zones(ctx.conn, ctx.store_id))
        if zone:
            state.zone_id, state.zone_name, state.pending = zone.id, zone.name, None
            return await _advance(ctx, state, notes)
        if len(_tokens(text)) <= 3:  # a short place name that isn't one of ours
            return await _decline_area(ctx)
    elif p.kind == "variant" and p.item:
        picked = parse_options(text, p.option_values)
        if picked:
            item = p.item.model_copy(update={**picked})
            state.pending = None
            state.queue.insert(0, item)
            return await _advance(ctx, state, notes)
    elif p.kind == "product" and p.item:
        norm = normalize_arabic(text)
        chosen = [c for c in p.candidates if normalize_arabic(c) in norm or norm in normalize_arabic(c)]
        if len(chosen) == 1:
            state.queue.insert(0, p.item.model_copy(update={"product_text": chosen[0]}))
            state.pending = None
            return await _advance(ctx, state, notes)
    return None


async def _answer_stock(ctx: Context, state: BotState, yes: bool, notes: list[str]) -> Outcome:
    p = state.pending
    state.pending = None
    if p and p.stock_item:
        if yes and p.available:
            _merge(state.items, p.stock_item.model_copy(update={"quantity": p.available}))
        else:
            notes.append(f"تمام، شلت {_label(p.stock_item)} من الطلب.")
    return await _advance(ctx, state, notes)


def _apply_analysis(state: BotState, analysis, text: str) -> bool:
    """Fold LLM output into the draft. Returns True if anything changed."""
    changed = False
    p = state.pending
    items = list(analysis.items)

    # An answer to "which color/size?" often comes back as an item with just options.
    if p and p.kind == "variant" and p.item and items:
        first = items[0]
        same_product = (
            not first.product_text.strip()
            or normalize_arabic(first.product_text) in normalize_arabic(p.item.product_text)
            or normalize_arabic(p.item.product_text) in normalize_arabic(first.product_text)
        )
        if same_product and (first.color or first.size):
            state.queue.insert(0, p.item.model_copy(update={
                "color": first.color or p.item.color, "size": first.size or p.item.size,
            }))
            state.pending = None
            items = items[1:]
            changed = True

    for it in items:
        state.queue.append(QueuedItem(product_text=it.product_text, color=it.color, size=it.size,
                                      quantity=it.quantity, notes=it.notes))
        changed = True

    if analysis.address_text and analysis.address_text != state.address:
        state.address, state.zone_id, state.zone_name = analysis.address_text, None, None
        if p and p.kind in ("address", "zone"):
            state.pending = None
        changed = True
    elif p and p.kind == "address" and not items and analysis.intent in ("order", "other"):
        # Asked for the address and got a plain reply: take it as the address.
        state.address, state.zone_id, state.zone_name, state.pending = text.strip(), None, None, None
        changed = True

    if analysis.phone and analysis.phone != state.phone:
        state.phone = analysis.phone
        if p and p.kind == "phone":
            state.pending = None
        changed = True

    if changed and state.mode == "idle":
        state.mode = "collecting"
    return changed


async def _advance(ctx: Context, state: BotState, notes: list[str]) -> Outcome:
    """Resolve what's missing, one question at a time, then show the summary."""
    if state.mode == "idle":
        state.mode = "collecting"
    if state.pending:
        return Outcome(_reply(_with_notes(notes, state.pending.question), _pending_buttons(state.pending)), state)

    # Items: match queued ones to variants.
    while state.queue:
        q = state.queue.pop(0)
        m = await match_item(ctx.conn, ctx.store_id, q.product_text, q.color, q.size, q.quantity)
        if m.status == "matched" and m.stock_ok:
            _merge(state.items, DraftItem(variant_id=m.variant_id, product_id=m.product_id, product_name=m.product_name,
                                          variant_name=m.variant_name, quantity=q.quantity, notes=q.notes))
            continue
        question = clarifying_question(m)
        if m.status == "matched":  # not enough stock
            if not m.stock_quantity:
                # Sold out: keep the product and offer the options still in stock, like staff do
                # (archive eval 3.7). Nothing left at all -> say so and drop it.
                options = await product_matching.in_stock_options(ctx.conn, ctx.store_id, m.product_id)
                if not options:
                    notes.append(question)
                    continue
                offer = "؛ ".join(f"{OPTION_LABELS[k]}: {'، '.join(v)}" for k, v in options.items())
                state.pending = Pending(
                    kind="variant", question=f"{question} المتوفر منه: {offer}. أي واحد بدك؟",
                    item=q.model_copy(update={"product_text": m.product_name, "color": None, "size": None}),
                    missing=list(options), option_values=options,
                )
                return Outcome(_reply(_with_notes(notes, state.pending.question)), state)
            item = DraftItem(variant_id=m.variant_id, product_id=m.product_id, product_name=m.product_name,
                             variant_name=m.variant_name, quantity=q.quantity, notes=q.notes)
            state.pending = Pending(kind="stock", question=question, stock_item=item, available=m.stock_quantity)
        elif m.status in ("needs_variant", "variant_unavailable"):
            if m.status == "needs_variant":
                item = q.model_copy(update={"product_text": m.product_name})
            else:  # that combination doesn't exist: let the customer pick again from what does
                item = q.model_copy(update={"product_text": m.product_name, "color": None, "size": None})
            state.pending = Pending(kind="variant", question=question, item=item,
                                    missing=m.missing_options, option_values=m.option_values)
        elif m.status == "ambiguous_product":
            state.pending = Pending(kind="product", question=question, item=q, candidates=m.candidates)
        else:  # not_found
            notes.append(question)
            continue
        return Outcome(_reply(_with_notes(notes, state.pending.question), _pending_buttons(state.pending)), state)

    if not state.items:
        text = _with_notes(notes, "شو بتحب تطلب؟") if notes else WELCOME
        return Outcome(_reply(text), state)

    # Large order (code, from store limits): the owner handles it.
    try:
        early = await order_totals.compute_totals(ctx.conn, ctx.store_id, _lines(state), Decimal("0"))
    except order_totals.VariantGoneError:
        early = None  # handled with a clear note at the summary
    if early and order_totals.is_large_order(early, ctx.large_item_qty, ctx.large_total_qty, ctx.large_amount):
        return await _escalate(ctx, state, _describe(state), "large_order")

    if not state.address:
        state.pending = Pending(kind="address", question=ASK_ADDRESS)
        return Outcome(_reply(_with_notes(notes, ASK_ADDRESS)), state)

    if not state.zone_id:
        zones = await delivery.active_zones(ctx.conn, ctx.store_id)
        if not zones:
            # No zones set up at all is a store setup problem, not an out-of-area customer:
            # the owner must know, and we never invent a fee (hard rule 4).
            return await _escalate(ctx, state, f"{_describe(state)} - العنوان: {state.address}", "unknown_zone")
        zone = delivery.find_zone(state.address, zones)
        if zone:
            state.zone_id, state.zone_name = zone.id, zone.name
        else:
            names = "، ".join(z.name for z in zones)
            question = f"لأي منطقة التوصيل؟ منوصّل على: {names}."
            state.pending = Pending(kind="zone", question=question)
            return Outcome(_reply(_with_notes(notes, question)), state)


    if not state.phone and ctx.customer_phone:
        state.phone = ctx.customer_phone
    if not state.phone:
        state.pending = Pending(kind="phone", question=ASK_PHONE)
        return Outcome(_reply(_with_notes(notes, ASK_PHONE)), state)

    return await _summary(ctx, state, notes)


def _lines(state: BotState) -> list[tuple[UUID, int]]:
    return [(i.variant_id, i.quantity) for i in state.items]


def _describe(state: BotState) -> str:
    return "طلب: " + "، ".join(f"{_label(i)} × {i.quantity}" for i in state.items)


def _with_notes(notes: list[str], text: str) -> str:
    return "\n".join([*notes, text]) if notes else text


async def _zone_fee(ctx: Context, zone_id: UUID) -> Decimal | None:
    return await ctx.conn.fetchval(
        "select fee from delivery_zones where id = $1 and store_id = $2 and is_active", zone_id, ctx.store_id
    )


async def _summary(ctx: Context, state: BotState, notes: list[str]) -> Outcome:
    fee = await _zone_fee(ctx, state.zone_id) if state.zone_id else None
    if fee is None:  # zone was removed/paused meanwhile: ask again
        state.zone_id = state.zone_name = None
        return await _advance(ctx, state, notes)
    try:
        totals = await order_totals.compute_totals(ctx.conn, ctx.store_id, _lines(state), fee)
    except order_totals.VariantGoneError:
        gone = await ctx.conn.fetch(
            "select id from product_variants where store_id = $1 and id = any($2::uuid[]) and is_active",
            ctx.store_id, [i.variant_id for i in state.items],
        )
        alive = {r["id"] for r in gone}
        removed = [i for i in state.items if i.variant_id not in alive]
        state.items = [i for i in state.items if i.variant_id in alive]
        notes.extend(f"{_label(i)} ما عاد متوفر، شلناه من الطلب." for i in removed)
        return await _advance(ctx, state, notes)

    if order_totals.is_large_order(totals, ctx.large_item_qty, ctx.large_total_qty, ctx.large_amount):
        return await _escalate(ctx, state, _describe(state), "large_order")

    c = ctx.currency
    lines = []
    for line in totals.lines:
        label = line.product_name if line.variant_name == "الافتراضي" else f"{line.product_name} ({line.variant_name})"
        text = f"• {label} × {line.quantity} = {_money(line.line_total, c)}"
        if line.offer_name:
            text += f" (عرض «{line.offer_name}»: {line.unit_price:.2f} بدل {line.list_price:.2f} للقطعة)"
        lines.append(text)
    delivery_line = (
        f"التوصيل ({state.zone_name}): مجاني (عرض «{totals.free_delivery_offer}»)"
        if totals.free_delivery_offer else f"التوصيل ({state.zone_name}): {_money(totals.delivery_fee, c)}"
    )
    summary = "\n".join([
        "ملخص طلبك:", *lines,
        f"المجموع: {_money(totals.subtotal, c)}", delivery_line,
        f"الإجمالي: {_money(totals.total, c)}",
        f"العنوان: {state.address}", f"الهاتف: {state.phone}",
        "بتأكد الطلب؟",
    ])
    state.mode, state.pending, state.quoted_total = "awaiting_confirmation", None, totals.total
    return Outcome(_reply(_with_notes(notes, summary), CONFIRM_BUTTONS), state)


async def _confirm(ctx: Context, state: BotState, notes: list[str]) -> Outcome:
    """Customer pressed confirm: re-check everything in code, then place the order atomically (1.9)."""
    fee = await _zone_fee(ctx, state.zone_id) if state.zone_id else None
    if fee is None or not (state.items and state.address and state.phone):
        state.mode = "collecting"
        return await _advance(ctx, state, notes)
    try:
        totals = await order_totals.compute_totals(ctx.conn, ctx.store_id, _lines(state), fee)
    except order_totals.VariantGoneError:
        return await _summary(ctx, state, notes)  # drops the gone item with a note, re-quotes
    if state.quoted_total is None or totals.total != state.quoted_total:
        notes.append("انتبه: الأسعار أو العروض تغيّرت من آخر ملخص. هاد الملخص المحدّث:")
        return await _summary(ctx, state, notes)

    # Same items as one of this customer's orders from the last 24 h: ask before saving (3.5).
    earlier = await orders.find_recent_duplicate(
        ctx.conn, ctx.store_id, ctx.customer_id, [(line.variant_id, line.quantity) for line in totals.lines])
    if earlier and earlier.id != state.duplicate_ok_for:
        state.pending = Pending(
            kind="duplicate", duplicate_of=earlier.id,
            question=(f"انتبه: طلبت نفس هالطلب من شوي (طلب #{earlier.order_number}). "
                      "هاد طلب جديد زيادة على الأول؟"),
        )
        return Outcome(_reply(_with_notes(notes, state.pending.question), YES_NO_BUTTONS), state)

    try:
        placed = await orders.place_order(
            ctx.conn, ctx.store_id, customer_id=ctx.customer_id, conversation_id=ctx.conversation_id,
            totals=totals, zone_id=state.zone_id, address=state.address, phone=state.phone,
            notes={i.variant_id: i.notes for i in state.items},
            possible_duplicate_of=earlier.id if earlier else None,
        )
    except orders.InsufficientStockError as short:
        # Someone bought it meanwhile. Nothing was saved; offer what's left.
        item = next(i for i in state.items if i.variant_id == short.variant_id)
        state.items = [i for i in state.items if i.variant_id != short.variant_id]
        state.mode = "collecting"
        if short.available <= 0:
            notes.append(f"للأسف {_label(item)} خلص هلأ من المخزون، شلناه من الطلب.")
        else:
            state.pending = Pending(
                kind="stock", stock_item=item, available=short.available,
                question=f"للأسف ما ضل من {_label(item)} غير {short.available}. بتحب تاخد {short.available}؟",
            )
        return await _advance(ctx, state, notes)

    text = "\n".join([
        "تم تأكيد طلبك ✓",
        f"رقم الطلب: #{placed.order_number}",
        f"الإجمالي: {_money(placed.total, ctx.currency)}",
        f"رح نتواصل معك على {state.phone} لترتيب التوصيل. شكراً إلك!",
        *([f"تابع حالة طلبك هون: {_tracking_link(ctx, placed.tracking_token)}"] if placed.tracking_token else []),
    ])
    return Outcome(_reply(text), _reset())
