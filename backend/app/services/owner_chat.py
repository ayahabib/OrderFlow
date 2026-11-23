"""Owner stock chat without the LLM (task 3.3): turn a tool call into a reply or a pending action,
and run a pending action only after the owner confirms (hard rule 7).

All numbers come from the database and code (hard rule 1): the current stock, old -> new, and
the refusal when the result would be negative. Every query filters by store_id (hard rule 3).
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import asyncpg

from app.llm.owner_tools import GetStockStatus, ProposeStockAdjustment
from app.services import product_matching
from app.services.excel_import import DEFAULT_VARIANT
from app.services.product_matching import MatchResult

MAX_STOCK = 1_000_000  # same limit as editing stock on the products page

HELP = ("بقدر أساعدك بالمخزون بس. مثلاً: \"نقّص الفستان الأحمر M بمقدار 5\"، "
        "\"زيد 10 من القميص الأبيض L\"، \"خلّي الشنطة السودا 7\"، أو \"قديش باقي من الفستان؟\"")


@dataclass
class Proposal:
    action_id: UUID
    product_name: str
    variant_name: str
    old_stock: int
    new_stock: int
    expires_at: datetime


@dataclass
class ChatResult:
    reply: str
    proposal: Proposal | None = None


class ActionError(Exception):
    """A pending action can't run. `code` maps to an HTTP status; the message is for the owner."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code  # not_found | expired | invalid


@dataclass
class Executed:
    product_name: str
    variant_name: str
    old_stock: int
    new_stock: int


def label(product: str, variant: str) -> str:
    # A product without options has one variant called "الافتراضي"; showing it adds nothing.
    return product if not variant or variant == DEFAULT_VARIANT else f"{product} ({variant})"


def _unmatched_reply(m: MatchResult) -> str:
    """The product/variant wasn't pinned down: ask, never guess (hard rule 4)."""
    if m.status == "not_found":
        hint = f" الأقرب: {'، '.join(m.suggestions)}." if m.suggestions else ""
        return "ما لقيت هالمنتج بالمتجر." + hint
    if m.status == "ambiguous_product":
        return f"قصدك أي واحد: {' أو '.join(m.candidates)}؟"
    labels = product_matching.OPTION_LABELS
    if m.status == "needs_variant":
        parts = [f"{labels[k]} ({'، '.join(m.option_values.get(k, []))})" for k in m.missing_options]
        return f"أي {' و'.join(parts)} من {m.product_name}؟"
    offer = "؛ ".join(f"{labels[k]}: {'، '.join(v)}" for k, v in m.option_values.items() if v)
    return f"ما في هالخيار من {m.product_name}." + (f" الموجود: {offer}" if offer else "")


async def propose_adjustment(conn: asyncpg.Connection, store_id: UUID, call: ProposeStockAdjustment) -> ChatResult:
    """Match the variant and store a pending action. Nothing about the stock changes here."""
    if (call.change is None) == (call.set_to is None):
        return ChatResult("ما فهمت الكمية. اكتب مثلاً \"نقّص 5\" أو \"خلّيه 7\".")
    m = await product_matching.match_item(conn, store_id, call.product_text, call.color, call.size)
    if m.status != "matched":
        return ChatResult(_unmatched_reply(m))

    name = label(m.product_name, m.variant_name)
    old = m.stock_quantity
    new = call.set_to if call.set_to is not None else old + call.change
    if new == old:
        return ChatResult(f"الكمية من {name} هي {old} أصلاً، ما في شي يتغيّر.")
    if new < 0:
        return ChatResult(f"ما بصير: الموجود من {name} {old} بس، والنتيجة رح تكون {new}. "
                          "ما في شي تغيّر.")
    if new > MAX_STOCK:
        return ChatResult(f"الكمية كبيرة كتير (الحد {MAX_STOCK}). ما في شي تغيّر.")

    row = await conn.fetchrow(
        """
        insert into owner_pending_actions (store_id, variant_id, change, set_to)
        values ($1, $2, $3, $4)
        returning id, expires_at
        """,
        store_id, m.variant_id, call.change, call.set_to,
    )
    verb = (f"رح أنقص {-call.change} من" if call.change is not None and call.change < 0
            else f"رح أزيد {call.change} على" if call.change is not None
            else "رح أعدّل")
    reply = f"{verb} {name}: الكمية من {old} إلى {new}. موافق؟"
    return ChatResult(reply, Proposal(row["id"], m.product_name, m.variant_name, old, new, row["expires_at"]))


async def stock_status(conn: asyncpg.Connection, store_id: UUID, call: GetStockStatus) -> ChatResult:
    """Read-only answer from the database. Several variants match -> list each one's stock."""
    m = await product_matching.match_item(conn, store_id, call.product_text, call.color, call.size)
    if m.status == "matched":
        return ChatResult(f"الموجود من {label(m.product_name, m.variant_name)}: {m.stock_quantity}.")
    if m.status != "needs_variant":
        return ChatResult(_unmatched_reply(m))
    variants = await product_matching.active_variants(conn, store_id, m.product_id)
    _, remaining, _ = product_matching.pick_variant(variants, call.color, call.size)
    lines = "\n".join(f"- {v['name']}: {v['stock_quantity']}" for v in remaining)
    return ChatResult(f"المخزون من {m.product_name} (المجموع {sum(v['stock_quantity'] for v in remaining)}):\n{lines}")


async def confirm(conn: asyncpg.Connection, store_id: UUID, action_id: UUID) -> Executed:
    """Run a pending action in ONE transaction (hard rule 2).

    The variant row is locked and the result recomputed from the stock *now*, so an order that
    came in after the proposal is counted; a result below zero is refused and nothing changes.
    """
    async with conn.transaction():
        action = await conn.fetchrow(
            """
            select id, variant_id, change, set_to, expires_at < now() as expired
              from owner_pending_actions
             where id = $1 and store_id = $2 and status = 'pending'
             for update
            """,
            action_id, store_id,
        )
        if action is None:
            raise ActionError("not_found", "هالإجراء مش موجود أو انتفذ أو انلغى من قبل.")
        if action["expired"]:
            # Close it, but let the transaction commit first: raising here would roll this back.
            await _resolve(conn, store_id, action_id, "cancelled")
        else:
            return await _execute(conn, store_id, action_id, action)
    raise ActionError("expired", "انتهت صلاحية هالإجراء (15 دقيقة). اكتب الطلب من جديد.")


async def _execute(conn, store_id: UUID, action_id: UUID, action) -> Executed:
    """Inside confirm's transaction: lock the variant, recompute, write stock + movement."""
    variant = await conn.fetchrow(
        """
        select v.stock_quantity, v.name as variant_name, p.name as product_name
          from product_variants v
          join products p on p.id = v.product_id and p.store_id = v.store_id
         where v.id = $1 and v.store_id = $2
           for update of v
        """,
        action["variant_id"], store_id,
    )
    if variant is None:  # deleted in between (the FK cascade usually removes the action too)
        raise ActionError("not_found", "المنتج انحذف، ما في شي تغيّر.")

    old = variant["stock_quantity"]
    new = action["set_to"] if action["set_to"] is not None else old + action["change"]
    if new < 0:
        raise ActionError("invalid", f"المخزون تغيّر من وقت الاقتراح: الموجود هلق {old}، "
                                     f"والنتيجة رح تكون {new}. ما في شي تغيّر.")
    if new != old:
        await conn.execute(
            "update product_variants set stock_quantity = $3, updated_at = now() where id = $1 and store_id = $2",
            action["variant_id"], store_id, new,
        )
        await conn.execute(
            """
            insert into inventory_movements (store_id, variant_id, change, reason)
            values ($1, $2, $3, 'chat_edit')
            """,
            store_id, action["variant_id"], new - old,
        )
    await _resolve(conn, store_id, action_id, "confirmed")
    return Executed(variant["product_name"], variant["variant_name"], old, new)


async def cancel(conn: asyncpg.Connection, store_id: UUID, action_id: UUID) -> bool:
    status = await conn.execute(
        """
        update owner_pending_actions set status = 'cancelled', resolved_at = now()
         where id = $1 and store_id = $2 and status = 'pending'
        """,
        action_id, store_id,
    )
    return status != "UPDATE 0"


async def _resolve(conn, store_id: UUID, action_id: UUID, status: str) -> None:
    await conn.execute(
        """
        update owner_pending_actions set status = $3::pending_action_status, resolved_at = now()
         where id = $1 and store_id = $2
        """,
        action_id, store_id, status,
    )
