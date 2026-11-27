"""Platform admin (tasks 5.2-5.4): admin-only access, suspend, demo flag, platform numbers,
and connecting a store's WhatsApp number from its request.

The overview reads every store in the DB, and the test DB also holds real data, so the number
tests compare before/after deltas inside the rolled-back transaction.
"""

import json
import uuid
from decimal import Decimal

import httpx
import pytest
from cryptography.fernet import Fernet

from app.channels import whatsapp_api
from app.channels.whatsapp import WhatsAppAdapter
from tests.authutil import bearer, client_for, insert_auth_user, make_token, patch_jwks
from tests.authutil import test_settings as make_settings

pytestmark = pytest.mark.db

PLATFORM_TOKEN = "EAAplatform-system-user-token-for-tests"
SETTINGS = make_settings(channel_encryption_key=Fernet.generate_key().decode(),
                         whatsapp_system_user_token=PLATFORM_TOKEN)


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch):
    patch_jwks(monkeypatch)


class FakeGraph:
    """Answers Meta's phone-number lookup; only the platform token is accepted."""

    def __init__(self, status: int = 200):
        self.status = status
        self.calls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.url.path)
        assert request.headers["authorization"] == f"Bearer {PLATFORM_TOKEN}"
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"code": 100, "message": "bad id"}})
        return httpx.Response(200, json={"display_phone_number": "+970 59 912 3456", "verified_name": "متجر سارة"})


@pytest.fixture
def graph(monkeypatch):
    fake = FakeGraph()
    monkeypatch.setattr(whatsapp_api, "make_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)))
    return fake


async def new_admin(conn):
    user = await insert_auth_user(conn)
    await conn.execute("insert into platform_admins (user_id) values ($1)", user)
    return bearer(make_token(user))


async def new_store(conn, name="متجر", **cols):
    owner = await insert_auth_user(conn)
    store = await conn.fetchval("insert into stores (owner_id, name) values ($1, $2) returning id", owner, name)
    for col, value in cols.items():
        assert col in ("is_demo", "currency", "created_at")
        await conn.execute(f"update stores set {col} = $2 where id = $1", store, value)
    return store, bearer(make_token(owner))


async def add_order(conn, store, total, status="delivered", external_id=None, by_bot=False):
    customer = await conn.fetchval(
        "insert into customers (store_id, channel, external_id) values ($1, 'web', $2) returning id",
        store, external_id or f"cust-{uuid.uuid4().hex}",
    )
    conv = None
    if by_bot:
        conv = await conn.fetchval(
            "insert into conversations (store_id, customer_id, channel) values ($1, $2, 'web') returning id", store, customer,
        )
    number = await conn.fetchval("select coalesce(max(order_number), 0) + 1 from orders where store_id = $1", store)
    await conn.execute(
        """insert into orders (store_id, order_number, customer_id, conversation_id, status, subtotal, total)
           values ($1, $2, $3, $4, $5::order_status, $6, $6)""",
        store, number, customer, conv, status, Decimal(total),
    )


ADMIN_ROUTES = [
    ("GET", "/api/admin/stores", None),
    ("GET", "/api/admin/overview", None),
    ("POST", f"/api/admin/stores/{uuid.uuid4()}/suspend", None),
    ("POST", f"/api/admin/stores/{uuid.uuid4()}/unsuspend", None),
    ("PATCH", f"/api/admin/stores/{uuid.uuid4()}", {"is_demo": True}),
    ("GET", "/api/admin/whatsapp-requests", None),
    ("POST", f"/api/admin/whatsapp-requests/{uuid.uuid4()}/connect", {"phone_number_id": "123456789"}),
    ("POST", f"/api/admin/whatsapp-requests/{uuid.uuid4()}/reject", {"reason": "لا"}),
]


async def test_admin_routes_refuse_store_owners_and_anonymous(db_tx):
    """Done-when: a user who isn't an admin gets 403 on every /api/admin/* route."""
    _, owner = await new_store(db_tx)
    async with client_for(db_tx) as c:
        for method, path, body in ADMIN_ROUTES:
            r = await c.request(method, path, headers=owner, json=body)
            assert r.status_code == 403, (method, path)
            assert (await c.request(method, path, json=body)).status_code == 401, (method, path)
        assert (await c.get("/api/me", headers=owner)).json()["is_admin"] is False


async def test_admin_sees_stores_without_customer_data(db_tx):
    admin = await new_admin(db_tx)
    store, _ = await new_store(db_tx, "متجر الأدمن")
    await add_order(db_tx, store, "50.00")
    await add_order(db_tx, store, "20.00", status="cancelled")
    async with client_for(db_tx) as c:
        assert (await c.get("/api/me", headers=admin)).json()["is_admin"] is True
        stores = (await c.get("/api/admin/stores", headers=admin)).json()
    row = next(s for s in stores if s["id"] == str(store))
    assert row["name"] == "متجر الأدمن" and row["orders_count"] == 1      # the cancelled order is not counted
    assert row["suspended_at"] is None and row["is_demo"] is False and row["channels"] == []
    assert set(row) == {"id", "name", "owner_email", "created_at", "currency", "is_demo", "suspended_at",
                        "bot_enabled", "channels", "last_activity", "orders_count"}


async def test_suspend_stops_the_bot_and_is_audited(db_tx):
    admin = await new_admin(db_tx)
    store, owner = await new_store(db_tx)
    async with client_for(db_tx) as c:
        assert (await c.post(f"/api/admin/stores/{store}/suspend", headers=admin)).status_code == 204
        assert (await c.get("/api/store", headers=owner)).json()["suspended_at"] is not None
        # The owner can't switch the bot back on while suspended.
        assert (await c.patch("/api/store", headers=owner, json={"bot_enabled": True})).status_code == 403

        session = (await c.post(f"/webhooks/web/{store}/sessions")).json()["session_token"]
        h = {"X-Session-Token": session}
        await c.post(f"/webhooks/web/{store}/messages", headers=h, json={"text": "مرحبا", "client_message_id": uuid.uuid4().hex})
        assert [m["sender"] for m in (await c.get(f"/webhooks/web/{store}/messages", headers=h)).json()] == ["customer"]

        assert (await c.post(f"/api/admin/stores/{store}/unsuspend", headers=admin)).status_code == 204
        await c.post(f"/webhooks/web/{store}/messages", headers=h, json={"text": "في حدا؟", "client_message_id": uuid.uuid4().hex})
        assert [m["sender"] for m in (await c.get(f"/webhooks/web/{store}/messages", headers=h)).json()] == \
            ["customer", "customer", "bot"]
        assert (await c.post(f"/api/admin/stores/{uuid.uuid4()}/suspend", headers=admin)).status_code == 404

    rows = await db_tx.fetch(
        "select actor::text as actor, action, after from audit_logs where store_id = $1 and actor = 'admin' order by created_at",
        store,
    )
    assert [r["action"] for r in rows] == ["admin.suspend_store", "admin.unsuspend_store"]
    assert all("admin_user_id" in json.loads(r["after"]) for r in rows)


async def overview(c, admin, **params):
    r = await c.get("/api/admin/overview", headers=admin, params=params)
    assert r.status_code == 200
    return r.json()


def sales(view, currency):
    return next((s for s in view["sales"] if s["currency"] == currency),
                {"orders": 0, "orders_by_bot": 0, "revenue": "0"})


async def test_overview_counts_real_sales_only(db_tx):
    """Done-when: two stores, cancelled and returned orders, a synthetic customer and a demo store."""
    admin = await new_admin(db_tx)
    async with client_for(db_tx) as c:
        before = await overview(c, admin)
        before_all = await overview(c, admin, include_demo="true")

        a, _ = await new_store(db_tx, "أ", currency="XTS")   # XTS: ISO test currency, so real data can't mix in
        b, _ = await new_store(db_tx, "ب", currency="XTS")
        demo, _ = await new_store(db_tx, "تجريبي", currency="XTS", is_demo=True)
        await add_order(db_tx, a, "100.00")
        await add_order(db_tx, a, "50.50", by_bot=True)
        await add_order(db_tx, b, "30.00")
        await add_order(db_tx, b, "999.00", status="cancelled")
        await add_order(db_tx, b, "888.00", status="returned")
        await add_order(db_tx, b, "777.00", external_id="synthetic-1")   # seed_sales.py data
        await add_order(db_tx, demo, "666.00")

        after = await overview(c, admin)
        after_all = await overview(c, admin, include_demo="true")

    assert after["stores_total"] - before["stores_total"] == 2            # the demo store is left out
    assert after_all["stores_total"] - before_all["stores_total"] == 3
    assert after["stores_new_this_month"] - before["stores_new_this_month"] == 2
    assert after["stores_active_30d"] - before["stores_active_30d"] == 2

    xts = sales(after, "XTS")
    assert (xts["orders"], Decimal(xts["revenue"]), xts["orders_by_bot"]) == (3, Decimal("180.50"), 1)
    xts_all = sales(after_all, "XTS")
    assert (xts_all["orders"], Decimal(xts_all["revenue"])) == (5, Decimal("1623.50"))   # + synthetic + demo
    assert after["orders_total"] - before["orders_total"] == 3

    top = {t["name"]: t["orders"] for t in after["top_stores"]}
    assert top.get("أ", 2) == 2 and "تجريبي" not in top

    # This week's growth bucket moved by the same amounts.
    last, last_before = after["growth"][-1], before["growth"][-1]
    assert len(after["growth"]) == 12
    assert last["orders"] - last_before["orders"] == 3
    assert last["new_stores"] - last_before["new_stores"] == 2
    assert Decimal(last["revenue"]["XTS"]) == Decimal("180.50")


async def test_overview_validates_period_and_flags_demo(db_tx):
    admin = await new_admin(db_tx)
    store, _ = await new_store(db_tx, currency="XTS")
    await add_order(db_tx, store, "10.00")
    async with client_for(db_tx) as c:
        assert (await c.get("/api/admin/overview", headers=admin, params={"period": "day"})).status_code == 422
        assert len((await overview(c, admin, period="month"))["growth"]) == 12
        assert sales(await overview(c, admin), "XTS")["orders"] == 1
        assert (await c.patch(f"/api/admin/stores/{store}", headers=admin, json={"is_demo": True})).status_code == 204
        assert sales(await overview(c, admin), "XTS")["orders"] == 0        # now left out
        assert (await c.patch(f"/api/admin/stores/{store}", headers=admin, json={"is_demo": "x"})).status_code == 422


async def test_whatsapp_request_connect_flow(db_tx, graph):
    """Done-when: request -> admin connects -> the number belongs to that store; one number, one store."""
    admin = await new_admin(db_tx)
    store, owner = await new_store(db_tx)
    other, other_owner = await new_store(db_tx)
    async with client_for(db_tx, settings=SETTINGS) as c:
        assert (await c.get("/api/channels/whatsapp/request", headers=owner)).json() is None
        body = {"phone": "0599 123 456", "display_name": "متجر سارة", "confirm_not_on_app": True}
        assert (await c.post("/api/channels/whatsapp/request", headers=owner,
                             json={**body, "confirm_not_on_app": False})).status_code == 422
        r = await c.post("/api/channels/whatsapp/request", headers=owner, json=body)
        assert r.status_code == 201 and r.json()["phone"] == "+970599123456" and r.json()["status"] == "pending"
        assert (await c.post("/api/channels/whatsapp/request", headers=owner, json=body)).status_code == 409

        queue = (await c.get("/api/admin/whatsapp-requests", headers=admin, params={"status": "pending"})).json()
        req = next(q for q in queue if q["store_id"] == str(store))
        assert req["store_name"] == "متجر"

        graph.status = 404
        bad = await c.post(f"/api/admin/whatsapp-requests/{req['id']}/connect", headers=admin,
                           json={"phone_number_id": "1234567890"})
        assert bad.status_code == 400                                       # nothing saved for a wrong id
        assert await db_tx.fetchval("select count(*) from store_channels where store_id = $1", store) == 0

        graph.status = 200
        ok = await c.post(f"/api/admin/whatsapp-requests/{req['id']}/connect", headers=admin,
                          json={"phone_number_id": "1234567890"})
        assert ok.status_code == 200 and ok.json()["verified_name"] == "متجر سارة"
        assert (await c.post(f"/api/admin/whatsapp-requests/{req['id']}/connect", headers=admin,
                             json={"phone_number_id": "1234567890"})).status_code == 409   # already handled

        status = (await c.get("/api/channels/whatsapp", headers=owner)).json()
        assert status["connected"] is True and status["display_phone_number"] == "+970 59 912 3456"
        assert (await c.get("/api/channels/whatsapp/request", headers=owner)).json()["status"] == "connected"

        # The same number can't go to a second store.
        await c.post("/api/channels/whatsapp/request", headers=other_owner, json=body)
        other_req = next(q for q in (await c.get("/api/admin/whatsapp-requests", headers=admin,
                                                 params={"status": "pending"})).json() if q["store_id"] == str(other))
        dup = await c.post(f"/api/admin/whatsapp-requests/{other_req['id']}/connect", headers=admin,
                           json={"phone_number_id": "1234567890"})
        assert dup.status_code == 409

    channel = await db_tx.fetchrow(
        "select external_id, credentials from store_channels where store_id = $1 and channel = 'whatsapp' and is_active", store,
    )
    assert channel["external_id"] == "1234567890"
    # No token stored for this number: it sends with the platform's System User token.
    adapter = WhatsAppAdapter.from_channel(SETTINGS, channel["external_id"], channel["credentials"])
    assert adapter._api._headers["Authorization"] == f"Bearer {PLATFORM_TOKEN}"
    assert await db_tx.fetchval(
        "select count(*) from audit_logs where store_id = $1 and action = 'admin.connect_whatsapp'", store) == 1


async def test_whatsapp_request_reject_and_cancel(db_tx):
    admin = await new_admin(db_tx)
    store, owner = await new_store(db_tx)
    body = {"phone": "+970599000111", "display_name": "متجري", "confirm_not_on_app": True}
    async with client_for(db_tx, settings=SETTINGS) as c:
        await c.post("/api/channels/whatsapp/request", headers=owner, json=body)
        assert (await c.delete("/api/channels/whatsapp/request", headers=owner)).status_code == 204
        assert (await c.delete("/api/channels/whatsapp/request", headers=owner)).status_code == 404

        req = (await c.post("/api/channels/whatsapp/request", headers=owner, json=body)).json()
        r = await c.post(f"/api/admin/whatsapp-requests/{req['id']}/reject", headers=admin, json={"reason": "الرقم مستخدم على التطبيق"})
        assert r.status_code == 204
        latest = (await c.get("/api/channels/whatsapp/request", headers=owner)).json()
        assert latest["status"] == "rejected" and latest["reject_reason"] == "الرقم مستخدم على التطبيق"
        # After a rejection the owner can ask again.
        assert (await c.post("/api/channels/whatsapp/request", headers=owner, json=body)).status_code == 201


async def test_connect_needs_the_platform_token(db_tx):
    admin = await new_admin(db_tx)
    store, owner = await new_store(db_tx)
    no_token = make_settings(channel_encryption_key=Fernet.generate_key().decode())
    async with client_for(db_tx, settings=no_token) as c:
        req = (await c.post("/api/channels/whatsapp/request", headers=owner,
                            json={"phone": "0599111222", "display_name": "متجر", "confirm_not_on_app": True})).json()
        r = await c.post(f"/api/admin/whatsapp-requests/{req['id']}/connect", headers=admin,
                         json={"phone_number_id": "1234567890"})
    assert r.status_code == 503
