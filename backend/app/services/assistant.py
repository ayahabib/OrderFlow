"""Dashboard assistant answers (task 3.13): every number comes from SQL, the text from code.

Same rules as the sales report: cancelled and returned orders are not sales, days are the
store's local days (stores.timezone, computed in Postgres). Every query filters by store_id.
"""

from decimal import Decimal
from uuid import UUID

import asyncpg

from app.llm.assistant_tools import LowStock, OrdersByStatus, SalesSummary, TopProducts
from app.llm.owner_tools import GetStockStatus
from app.services import owner_chat

HELP = ("بقدر أجاوبك من أرقام متجرك. اسألني مثلاً: \"قديش بعنا هالشهر؟\"، \"شو أكثر منتج انباع؟\"، "
        "\"كم طلب جديد عنا؟\"، \"شو اللي قرّب يخلص؟\" أو \"قديش باقي من الفستان الأحمر؟\". "
        "لتعديل المخزون استعمل شات المخزون بصفحة المخزون.")

PERIOD_LABEL = {
    "today": "اليوم", "yesterday": "مبارح", "this_week": "هالأسبوع", "last_7_days": "آخر 7 أيام",
    "this_month": "هالشهر", "last_30_days": "آخر 30 يوم", "all_time": "من أول ما فتح المتجر",
}

# (first day, last day) of each period, as SQL on the store's local date `d` (today).
# this_week starts on Saturday, as in the region's working week.
_RANGE_SQL = {
    "today": ("d", "d"),
    "yesterday": ("d - 1", "d - 1"),
    "this_week": ("d - ((extract(dow from d)::int + 1) % 7)", "d"),
    "last_7_days": ("d - 6", "d"),
    "this_month": ("date_trunc('month', d)::date", "d"),
    "last_30_days": ("d - 29", "d"),
    "all_time": ("'-infinity'::date", "d"),
}

NOT_SALES = "('cancelled', 'returned')"
STATUS_LABEL = {"new": "جديد", "in_progress": "قيد التجهيز", "shipped": "تم الشحن", "delivered": "تم التوصيل",
                "cancelled": "ملغي", "returned": "مرتجع"}


def _sales_cte(period: str) -> str:
    """CTEs: s = the store (local today `d`), o = its sales in the period. $1 is store_id."""
    first, last = _RANGE_SQL[period]   # period is a fixed Literal; only these snippets are inlined
    return f"""
        with s as (select timezone, currency, (now() at time zone timezone)::date as d from stores where id = $1),
             o as (
               select o.* from orders o, s
                where o.store_id = $1 and o.status not in {NOT_SALES}
                  and (o.created_at at time zone s.timezone)::date between {first} and {last}
             )
    """


def _money(v: Decimal, currency: str) -> str:
    return f"{v:.2f} {currency}"


async def sales_summary(conn: asyncpg.Connection, store_id: UUID, call: SalesSummary) -> str:
    row = await conn.fetchrow(
        _sales_cte(call.period) + """
        select count(*)::int as n, coalesce(sum(total), 0)::numeric(10,2) as revenue,
               (select currency from s) as currency
          from o
        """,
        store_id,
    )
    when = PERIOD_LABEL[call.period]
    if row["n"] == 0:
        return f"ما في مبيعات {when}."
    average = (row["revenue"] / row["n"]).quantize(Decimal("0.01"))
    return (f"{when}: {row['n']} طلب، مجموع المبيعات {_money(row['revenue'], row['currency'])}، "
            f"ومعدّل الطلب {_money(average, row['currency'])}. (بدون الملغي والمرتجع)")


async def top_products(conn: asyncpg.Connection, store_id: UUID, call: TopProducts) -> str:
    limit = min(max(call.limit or 5, 1), 10)
    rows = await conn.fetch(
        _sales_cte(call.period) + """
        select i.product_name, sum(i.quantity)::int as quantity
          from order_items i join o on o.id = i.order_id
         where i.store_id = $1
         group by i.product_name
         order by quantity desc, i.product_name
         limit $2
        """,
        store_id, limit,
    )
    when = PERIOD_LABEL[call.period]
    if not rows:
        return f"ما انباع إشي {when}."
    if len(rows) == 1 or limit == 1:
        return f"أكثر منتج انباع {when}: {rows[0]['product_name']} ({rows[0]['quantity']} قطعة)."
    lines = "\n".join(f"{n}. {r['product_name']}: {r['quantity']} قطعة" for n, r in enumerate(rows, 1))
    return f"أكثر المنتجات مبيعاً {when}:\n{lines}"


async def orders_by_status(conn: asyncpg.Connection, store_id: UUID, _: OrdersByStatus) -> str:
    rows = await conn.fetch(
        "select status::text as status, count(*)::int as n from orders where store_id = $1 group by status",
        store_id,
    )
    counts = {r["status"]: r["n"] for r in rows}
    if not counts:
        return "ما في طلبات لسّا."
    parts = [f"{STATUS_LABEL[s]}: {counts[s]}" for s in STATUS_LABEL if counts.get(s)]
    return "الطلبات حسب الحالة: " + "، ".join(parts) + "."


async def low_stock(conn: asyncpg.Connection, store_id: UUID, _: LowStock) -> str:
    rows = await conn.fetch(
        """
        select product_name, variant_name, stock_quantity from low_stock_variants
         where store_id = $1 order by stock_quantity, product_name, variant_name limit 20
        """,
        store_id,
    )
    if not rows:
        return "كل المنتجات فوق حد التنبيه."
    sold_out = [owner_chat.label(r["product_name"], r["variant_name"]) for r in rows if r["stock_quantity"] == 0]
    low = [f"{owner_chat.label(r['product_name'], r['variant_name'])} ({r['stock_quantity']})"
           for r in rows if r["stock_quantity"] > 0]
    parts = []
    if sold_out:
        parts.append("خلصان: " + "، ".join(sold_out))
    if low:
        parts.append("قرّب يخلص: " + "، ".join(low))
    return ".\n".join(parts) + "."


async def answer(conn: asyncpg.Connection, store_id: UUID, call) -> str:
    """Run the read-only tool the LLM picked. Anything else gets the help text."""
    if isinstance(call, SalesSummary):
        return await sales_summary(conn, store_id, call)
    if isinstance(call, TopProducts):
        return await top_products(conn, store_id, call)
    if isinstance(call, OrdersByStatus):
        return await orders_by_status(conn, store_id, call)
    if isinstance(call, LowStock):
        return await low_stock(conn, store_id, call)
    if isinstance(call, GetStockStatus):
        return (await owner_chat.stock_status(conn, store_id, call)).reply
    return HELP
