"""Place an order atomically (CLAUDE.md hard rule 2, DATABASE.md 5.1 and 5.2).

One transaction:
  next_order_number -> insert order + item snapshots -> for each variant
  `update ... set stock = stock - qty where stock >= qty` -> inventory_movements ('order').
If any variant is short, the whole thing rolls back and nothing is saved.
"""

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

import asyncpg

from app.services.order_totals import Totals


@dataclass(frozen=True)
class PlacedOrder:
    order_id: UUID
    order_number: int
    total: Decimal


class InsufficientStockError(Exception):
    """A variant didn't have enough stock at confirm time. Nothing was saved."""

    def __init__(self, variant_id: UUID, wanted: int, available: int):
        super().__init__(f"variant {variant_id}: wanted {wanted}, available {available}")
        self.variant_id = variant_id
        self.wanted = wanted
        self.available = available


async def place_order(
    conn: asyncpg.Connection,
    store_id: UUID,
    *,
    customer_id: UUID,
    conversation_id: UUID | None,
    totals: Totals,
    zone_id: UUID | None,
    address: str | None,
    phone: str | None,
    notes: dict[UUID, str | None] | None = None,
    possible_duplicate_of: UUID | None = None,
) -> PlacedOrder:
    """Save the order and deduct stock. Raises InsufficientStockError (after a full rollback)."""
    if not totals.lines or any(line.quantity <= 0 for line in totals.lines):
        raise ValueError("an order needs items with quantity > 0")
    notes = notes or {}

    async with conn.transaction():
        # Locks the store row, so order numbers stay sequential and unique per store.
        number = await conn.fetchval("select next_order_number($1)", store_id)
        if number is None:
            raise ValueError("unknown store")

        order_id = await conn.fetchval(
            """
            insert into orders (store_id, order_number, customer_id, conversation_id, status,
                                delivery_address, delivery_zone_id, delivery_fee, subtotal, total,
                                free_delivery_offer_id, possible_duplicate_of)
            values ($1, $2, $3, $4, 'new', $5, $6, $7, $8, $9, $10, $11)
            returning id
            """,
            store_id, number, customer_id, conversation_id, address, zone_id,
            totals.delivery_fee, totals.subtotal, totals.total, totals.free_delivery_offer_id,
            possible_duplicate_of,
        )

        # Snapshots: names and prices as the customer saw them (DATABASE.md principle 6).
        await conn.executemany(
            """
            insert into order_items (store_id, order_id, product_id, variant_id, product_name, variant_name,
                                     unit_price, list_price, offer_id, quantity, notes)
            values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            """,
            [(store_id, order_id, line.product_id, line.variant_id, line.product_name, line.variant_name,
              line.unit_price, line.list_price, line.offer_id, line.quantity, notes.get(line.variant_id))
             for line in totals.lines],
        )

        # Stock: conditional update per variant. Sorted by id so two orders always lock
        # rows in the same order (no deadlocks).
        for line in sorted(totals.lines, key=lambda l: str(l.variant_id)):
            left = await conn.fetchval(
                """
                update product_variants
                   set stock_quantity = stock_quantity - $3, updated_at = now()
                 where id = $1 and store_id = $2 and is_active and stock_quantity >= $3
                returning stock_quantity
                """,
                line.variant_id, store_id, line.quantity,
            )
            if left is None:
                available = await conn.fetchval(
                    "select stock_quantity from product_variants where id = $1 and store_id = $2 and is_active",
                    line.variant_id, store_id,
                )
                # Raising inside `async with conn.transaction()` rolls back the order, items and earlier updates.
                raise InsufficientStockError(line.variant_id, line.quantity, available or 0)
            await conn.execute(
                """
                insert into inventory_movements (store_id, variant_id, change, reason, order_id)
                values ($1, $2, $3, 'order', $4)
                """,
                store_id, line.variant_id, -line.quantity, order_id,
            )

        # Remember contact details for next time.
        await conn.execute(
            """
            update customers set phone = coalesce($3, phone), default_address = coalesce($4, default_address)
             where id = $1 and store_id = $2
            """,
            customer_id, store_id, phone, address,
        )

    return PlacedOrder(order_id=order_id, order_number=number, total=totals.total)


# --- status changes from the orders page (task 1.10) --------------------------------

DUPLICATE_HOURS = 24


@dataclass
class RecentOrder:
    id: UUID
    order_number: int


async def find_recent_duplicate(
    conn: asyncpg.Connection, store_id: UUID, customer_id: UUID, lines: list[tuple[UUID, int]],
) -> RecentOrder | None:
    """The customer's newest order from the last DUPLICATE_HOURS hours (not cancelled) with exactly
    the same items: same variants and quantities, in any order (task 3.5)."""
    wanted = Counter((variant_id, qty) for variant_id, qty in lines)
    rows = await conn.fetch(
        """
        select o.id, o.order_number,
               array(select i.variant_id from order_items i
                      where i.order_id = o.id and i.store_id = o.store_id order by i.id) as variant_ids,
               array(select i.quantity from order_items i
                      where i.order_id = o.id and i.store_id = o.store_id order by i.id) as quantities
          from orders o
         where o.store_id = $1 and o.customer_id = $2 and o.status <> 'cancelled'
           and o.created_at >= now() - make_interval(hours => $3)
         order by o.created_at desc
        """,
        store_id, customer_id, DUPLICATE_HOURS,
    )
    for r in rows:
        if Counter(zip(r["variant_ids"], r["quantities"])) == wanted:
            return RecentOrder(r["id"], r["order_number"])
    return None


# Forward only, plus cancel; delivered and cancelled are final (owner's decision), so
# stock can never be returned twice.
ALLOWED = {
    "new": {"in_progress", "delivered", "cancelled"},
    "in_progress": {"delivered", "cancelled"},
    "delivered": set(),
    "cancelled": set(),
}


class StatusChangeError(Exception):
    """Transition not allowed from the current status."""

    def __init__(self, current: str, wanted: str):
        super().__init__(f"{current} -> {wanted}")
        self.current = current
        self.wanted = wanted


async def change_status(conn: asyncpg.Connection, store_id: UUID, order_id: UUID, new_status: str) -> bool:
    """Change an order's status. Cancelling returns its stock with 'order_cancel' movements, atomically.

    Returns False if the order isn't in this store. Raises StatusChangeError for a disallowed move.
    """
    async with conn.transaction():
        # Lock the order so two clicks can't both cancel (and return stock twice).
        current = await conn.fetchval(
            "select status::text from orders where id = $1 and store_id = $2 for update", order_id, store_id
        )
        if current is None:
            return False
        if new_status == current:
            return True  # nothing to do (e.g. a double click)
        if new_status not in ALLOWED.get(current, set()):
            raise StatusChangeError(current, new_status)

        if new_status == "cancelled":
            items = await conn.fetch(
                """
                select variant_id, quantity from order_items
                 where order_id = $1 and store_id = $2 and variant_id is not null
                 order by variant_id
                """,
                order_id, store_id,
            )
            for item in items:
                # The variant may have been deactivated; its stock still comes back.
                updated = await conn.fetchval(
                    """
                    update product_variants set stock_quantity = stock_quantity + $3, updated_at = now()
                     where id = $1 and store_id = $2
                    returning id
                    """,
                    item["variant_id"], store_id, item["quantity"],
                )
                if updated:
                    await conn.execute(
                        """
                        insert into inventory_movements (store_id, variant_id, change, reason, order_id)
                        values ($1, $2, $3, 'order_cancel', $4)
                        """,
                        store_id, item["variant_id"], item["quantity"], order_id,
                    )

        await conn.execute(
            "update orders set status = $3::order_status, updated_at = now() where id = $1 and store_id = $2",
            order_id, store_id, new_status,
        )
    return True
