""""Tell me when it's back" (task 3.12).

The bot saves an interest only after the customer says yes. When the variant has stock again,
the owner sees who is waiting on the stock page and sends one fixed message (written here,
not by the LLM) to each of them. Every query filters by store_id.
"""

import logging
from dataclasses import dataclass
from uuid import UUID

import asyncpg

from app.config import Settings
from app.services import audit
from app.services import owner_messages

log = logging.getLogger(__name__)


async def register(conn: asyncpg.Connection, store_id: UUID, customer_id: UUID, variant_id: UUID) -> None:
    """Save the interest once (an open one for the same customer and variant is kept as is)."""
    result = await conn.execute(
        """
        insert into customer_interests (store_id, customer_id, variant_id)
        select $1, cu.id, v.id
          from customers cu, product_variants v
         where cu.id = $2 and cu.store_id = $1 and v.id = $3 and v.store_id = $1
        on conflict (store_id, customer_id, variant_id) where notified_at is null do nothing
        """,
        store_id, customer_id, variant_id,
    )
    if result.endswith(" 1"):   # "INSERT 0 1": a new one, not a repeat
        await audit.log(conn, store_id, "bot", "interest.create", variant_id, after={"customer_id": customer_id})


async def waiting(conn: asyncpg.Connection, store_id: UUID) -> list[asyncpg.Record]:
    """Variants with customers still waiting, most waited-for first."""
    return await conn.fetch(
        """
        select v.id as variant_id, p.name as product_name, v.name as variant_name, v.stock_quantity,
               count(*)::int as waiting, min(i.created_at) as since,
               array_agg(coalesce(cu.name, 'بدون اسم') order by i.created_at) as customer_names
          from customer_interests i
          join product_variants v on v.id = i.variant_id and v.store_id = i.store_id
          join products p on p.id = v.product_id and p.store_id = v.store_id
          join customers cu on cu.id = i.customer_id and cu.store_id = i.store_id
         where i.store_id = $1 and i.notified_at is null
         group by v.id, p.name, v.name, v.stock_quantity
         order by (v.stock_quantity > 0) desc, count(*) desc, min(i.created_at)
        """,
        store_id,
    )


def back_in_stock_text(product_name: str, variant_name: str | None) -> str:
    label = product_name if not variant_name or variant_name == "الافتراضي" else f"{product_name} ({variant_name})"
    return f"خبر حلو! {label} رجع متوفر عنا. إذا لسا بدك ياه احكيلي وبسجّلك الطلب."


class NothingInStock(Exception):
    pass


@dataclass
class NotifyResult:
    sent: int
    failed: int


async def notify(conn: asyncpg.Connection, settings: Settings, store_id: UUID, variant_id: UUID) -> NotifyResult:
    """Send the "it's back" message to everyone waiting for this variant, on their latest conversation.

    Raises NothingInStock when the variant is still sold out (or not in this store).
    """
    variant = await conn.fetchrow(
        """
        select p.name as product_name, v.name as variant_name, v.stock_quantity
          from product_variants v join products p on p.id = v.product_id and p.store_id = v.store_id
         where v.id = $1 and v.store_id = $2
        """,
        variant_id, store_id,
    )
    if variant is None or variant["stock_quantity"] <= 0:
        raise NothingInStock()
    text = back_in_stock_text(variant["product_name"], variant["variant_name"])

    rows = await conn.fetch(
        """
        select i.id as interest_id, c.id, c.channel, cu.external_id
          from customer_interests i
          join customers cu on cu.id = i.customer_id and cu.store_id = i.store_id
          join lateral (
                select id, channel from conversations
                 where store_id = i.store_id and customer_id = i.customer_id
                 order by last_message_at desc limit 1
               ) c on true
         where i.store_id = $1 and i.variant_id = $2 and i.notified_at is null
        """,
        store_id, variant_id,
    )
    sent = failed = 0
    for row in rows:
        try:
            async with conn.transaction():
                adapter = await owner_messages.store(conn, settings, store_id, row, text)
                await conn.execute(
                    "update customer_interests set notified_at = now() where id = $1 and store_id = $2",
                    row["interest_id"], store_id,
                )
        except owner_messages.ChannelUnavailable:
            failed += 1  # e.g. the Telegram bot was disconnected: the interest stays open
            continue
        await owner_messages.deliver(adapter, row, text)
        sent += 1
    return NotifyResult(sent=sent, failed=failed)


