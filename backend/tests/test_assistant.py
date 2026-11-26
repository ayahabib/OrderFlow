"""Dashboard assistant (TASKS.md 3.13): read-only tools, numbers from SQL, nothing written."""

import pytest

from app.llm.assistant_tools import LowStock, OrdersByStatus, SalesSummary, TopProducts
from app.llm.client import LLMError
from app.llm.owner_tools import GetStockStatus
from app.services.assistant import HELP
from tests.authutil import ScriptedLLM, bearer, client_for, make_token, patch_jwks
from tests.test_orders import make_store, order

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch):
    patch_jwks(monkeypatch)


TOOLS = {
    "قديش بعنا اليوم؟": SalesSummary(period="today"),
    "قديش بعنا مبارح؟": SalesSummary(period="yesterday"),
    "شو أكثر منتج انباع هالشهر؟": TopProducts(period="this_month", limit=1),
    "أكثر 5 منتجات": TopProducts(period="all_time", limit=50),     # limit is clamped by code
    "كم طلب عنا؟": OrdersByStatus(),
    "شو قرّب يخلص؟": LowStock(),
    "قديش باقي من القميص الأسود؟": GetStockStatus(product_text="قميص", color="أسود", size=None),
}


async def ask(c, h, text):
    r = await c.post("/api/assistant", headers=h, json={"text": text})
    assert r.status_code == 200, r.text
    return r.json()["reply"]


async def test_answers_come_from_the_database(db_tx):
    s = await make_store(db_tx, stock_a=10, stock_b=3)
    h = bearer(make_token(s["owner"]))
    await order(db_tx, s, [(s["a"], 2)])                       # 100 + 10 = 110
    second = await order(db_tx, s, [(s["a"], 1), (s["b"], 1)]) # 80 + 10 = 90
    cancelled = await order(db_tx, s, [(s["b"], 1)])
    await db_tx.execute("update orders set status = 'cancelled' where id = $1", cancelled.order_id)
    await db_tx.execute("update orders set status = 'shipped' where id = $1", second.order_id)
    before = await db_tx.fetchval("select sum(stock_quantity) from product_variants where store_id = $1", s["store"])

    async with client_for(db_tx, llm=ScriptedLLM(tools=TOOLS)) as c:
        assert await ask(c, h, "قديش بعنا اليوم؟") == (
            "اليوم: 2 طلب، مجموع المبيعات 200.00 ILS، ومعدّل الطلب 100.00 ILS. (بدون الملغي والمرتجع)")
        assert await ask(c, h, "قديش بعنا مبارح؟") == "ما في مبيعات مبارح."
        assert await ask(c, h, "شو أكثر منتج انباع هالشهر؟") == "أكثر منتج انباع هالشهر: قميص (4 قطعة)."
        assert await ask(c, h, "أكثر 5 منتجات") == "أكثر منتج انباع من أول ما فتح المتجر: قميص (4 قطعة)."   # one product: one line
        assert await ask(c, h, "كم طلب عنا؟") == "الطلبات حسب الحالة: جديد: 1، تم الشحن: 1، ملغي: 1."
        # Black: 3 - 1 - 1 = 1 left (the test cancelled by SQL, so no stock came back). White: 7, above the alert.
        assert await ask(c, h, "شو قرّب يخلص؟") == "قرّب يخلص: قميص (أسود) (1)."
        # The test variants have names but no color options, so all of the shirt is listed (stock chat 3.3).
        assert "- أسود: 1" in await ask(c, h, "قديش باقي من القميص الأسود؟")

    after = await db_tx.fetchval("select sum(stock_quantity) from product_variants where store_id = $1", s["store"])
    assert after == before                                       # read only


async def test_no_tool_gets_help_and_llm_failure_is_503(db_tx):
    s = await make_store(db_tx)
    h = bearer(make_token(s["owner"]))
    async with client_for(db_tx, llm=ScriptedLLM(tools={"نقّص القميص 5": None, "قديش بعنا اليوم؟": LLMError("timeout", "x")})) as c:
        assert await ask(c, h, "نقّص القميص 5") == HELP          # changes are not this assistant's job
        r = await c.post("/api/assistant", headers=h, json={"text": "قديش بعنا اليوم؟"})
        assert r.status_code == 503


async def test_other_store_numbers_are_invisible(db_tx):
    s = await make_store(db_tx)
    other = await make_store(db_tx)
    await order(db_tx, other, [(other["a"], 1)])
    async with client_for(db_tx, llm=ScriptedLLM(tools=TOOLS)) as c:
        assert await ask(c, bearer(make_token(s["owner"])), "قديش بعنا اليوم؟") == "ما في مبيعات اليوم."
