"""WhatsApp Cloud API channel end to end with a fake Graph API (TASKS.md 5.1).

Done-when: a full order conversation works from WhatsApp (here with the fake API; the real
test number is tried by hand). Hard rule 12: unsigned or wrongly signed calls are refused.
"""

import hashlib
import hmac
import json

import httpx
import pytest
from cryptography.fernet import Fernet

from app.channels import whatsapp as wa
from app.channels import whatsapp_api
from app.channels.base import Button, OutboundMessage
from app.utils.crypto import decrypt_json, encrypt_json
from tests.authutil import ScriptedLLM, analysis, bearer, client_for, make_token, patch_jwks
from tests.authutil import test_settings as make_settings
from tests.test_order_flow import ASK_ADDRESS, item, new_store

APP_SECRET = "test-app-secret"
VERIFY = "test-verify-token"
SETTINGS = make_settings(whatsapp_app_secret=APP_SECRET, whatsapp_verify_token=VERIFY,
                         channel_encryption_key=Fernet.generate_key().decode(),
                         public_base_url="https://example.ngrok.app")
PHONE_ID = "1098765432101"
TOKEN = "EAAG" + "x" * 60
CUSTOMER = "970599123456"


def sign(body: bytes, secret: str = APP_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def text_event(text: str, msg_id: str, phone_id: str = PHONE_ID) -> dict:
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"phone_number_id": phone_id, "display_phone_number": "15550001111"},
        "contacts": [{"wa_id": CUSTOMER, "profile": {"name": "سارة"}}],
        "messages": [{"from": CUSTOMER, "id": msg_id, "timestamp": "1", "type": "text", "text": {"body": text}}],
    }}]}]}


class FakeGraph:
    """Records every Graph API call and answers like Meta would."""

    def __init__(self, phone_status: int = 200):
        self.calls: list[tuple[str, str, dict]] = []
        self.phone_status = phone_status

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        self.calls.append((request.method, request.url.path, body))
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        if request.method == "GET":
            if self.phone_status != 200:
                return httpx.Response(self.phone_status, json={"error": {"code": 100, "message": "bad id"}})
            return httpx.Response(200, json={"display_phone_number": "+1 555-000-1111", "verified_name": "Test Store"})
        return httpx.Response(200, json={"messages": [{"id": "wamid.out"}]})

    def sent(self) -> list[dict]:
        return [b for m, p, b in self.calls if m == "POST" and p.endswith("/messages")]


@pytest.fixture
def graph(monkeypatch):
    fake = FakeGraph()
    monkeypatch.setattr(whatsapp_api, "make_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)))
    return fake


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch):
    patch_jwks(monkeypatch)


# --- pure functions (no DB) ---------------------------------------------------------------

def test_signature_check():
    body = b'{"a":1}'
    assert wa.verify_signature(SETTINGS, body, sign(body))
    assert not wa.verify_signature(SETTINGS, body, sign(body, "other-secret"))
    assert not wa.verify_signature(SETTINGS, body, None)
    assert not wa.verify_signature(SETTINGS, b'{"a":2}', sign(body))   # body changed after signing


def test_parse_text_button_media_and_statuses():
    payload = text_event("بدي فستان", "wamid.1")
    value = payload["entry"][0]["changes"][0]["value"]
    value["messages"] += [
        {"from": CUSTOMER, "id": "wamid.2", "type": "interactive", "interactive": {"button_reply": {"id": "confirm", "title": "تأكيد"}}},
        {"from": CUSTOMER, "id": "wamid.3", "type": "image", "image": {"id": "x"}},
    ]
    value["statuses"] = [{"id": "wamid.out", "status": "read"}]
    parsed = wa.parse_payload(payload)
    assert [(p.text, p.is_button, p.external_message_id) for p in parsed] == [
        ("بدي فستان", False, "wa:wamid.1"), ("تأكيد", True, "wa:wamid.2"), (None, False, "wa:wamid.3")]
    assert parsed[0].customer_name == "سارة" and parsed[0].phone_number_id == PHONE_ID


def test_buttons_become_interactive_reply_buttons():
    msg = OutboundMessage(text="بتأكد الطلب؟", buttons=[Button(id="confirm", title="تأكيد"), Button(id="edit", title="تعديل")])
    payload = wa.message_payload(CUSTOMER, msg)
    assert payload["type"] == "interactive"
    assert [b["reply"]["title"] for b in payload["interactive"]["action"]["buttons"]] == ["تأكيد", "تعديل"]
    assert wa.message_payload(CUSTOMER, OutboundMessage(text="هلا"))["type"] == "text"


# --- webhook + connect (DB) ---------------------------------------------------------------

async def connect(conn, store):
    creds = encrypt_json(SETTINGS, {"access_token": TOKEN, "display_phone_number": "+1 555-000-1111"})
    await conn.execute(
        "insert into store_channels (store_id, channel, external_id, credentials) values ($1, 'whatsapp', $2, $3)",
        store, PHONE_ID, creds)


async def post(c, payload: dict, signature: str | None = "auto"):
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if signature:
        headers["X-Hub-Signature-256"] = sign(body) if signature == "auto" else signature
    return await c.post("/webhooks/whatsapp", content=body, headers=headers)


@pytest.mark.db
async def test_meta_verification_handshake(db_tx):
    async with client_for(db_tx, SETTINGS) as c:
        ok = await c.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "12345"})
        bad = await c.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "12345"})
    assert (ok.status_code, ok.text) == (200, "12345")
    assert bad.status_code == 403


@pytest.mark.db
async def test_unsigned_or_forged_calls_are_refused(db_tx, graph):
    store = await new_store(db_tx)
    await connect(db_tx, store)
    async with client_for(db_tx, SETTINGS) as c:
        assert (await post(c, text_event("مرحبا", "wamid.a"), signature=None)).status_code == 403
        assert (await post(c, text_event("مرحبا", "wamid.a"), signature=sign(b"other"))).status_code == 403
    assert await db_tx.fetchval("select count(*) from messages where store_id = $1", store) == 0
    assert graph.sent() == []


@pytest.mark.db
async def test_order_conversation_over_whatsapp(db_tx, graph):
    """Done-when (with the fake API): messages reach the engine and the replies go back on WhatsApp."""
    store = await new_store(db_tx)
    await connect(db_tx, store)
    llm = ScriptedLLM({"بدي فستانين": analysis(items=[item("فستان", qty=2)])})
    async with client_for(db_tx, SETTINGS, llm=llm) as c:
        assert (await post(c, text_event("بدي فستانين", "wamid.1"))).status_code == 200
        assert (await post(c, text_event("بدي فستانين", "wamid.1"))).status_code == 200   # Meta retry: ignored
        assert (await post(c, text_event("أحمر M", "wamid.2"))).status_code == 200
    sent = graph.sent()
    assert [s["to"] for s in sent] == [CUSTOMER, CUSTOMER]
    assert "أي اللون" in sent[0]["text"]["body"] and sent[1]["text"]["body"] == ASK_ADDRESS
    customer = await db_tx.fetchrow("select channel::text as channel, external_id, name from customers where store_id = $1", store)
    assert tuple(customer) == ("whatsapp", CUSTOMER, "سارة")
    assert await db_tx.fetchval("select count(*) from messages where store_id = $1 and sender = 'customer'", store) == 2


@pytest.mark.db
async def test_unknown_number_and_media(db_tx, graph):
    store = await new_store(db_tx)
    await connect(db_tx, store)
    image = text_event("x", "wamid.img")
    image["entry"][0]["changes"][0]["value"]["messages"][0] = {"from": CUSTOMER, "id": "wamid.img", "type": "image", "image": {}}
    async with client_for(db_tx, SETTINGS) as c:
        assert (await post(c, text_event("مرحبا", "wamid.x", phone_id="999999999"))).status_code == 200   # no store: dropped
        assert (await post(c, image)).status_code == 200
    assert [s["text"]["body"] for s in graph.sent()] == [wa.UNSUPPORTED_REPLY]


@pytest.mark.db
async def test_connect_checks_the_token_and_stores_it_encrypted(db_tx, graph):
    store = await new_store(db_tx)
    h = bearer(make_token(await db_tx.fetchval("select owner_id from stores where id = $1", store)))
    async with client_for(db_tx, SETTINGS) as c:
        r = await c.post("/api/channels/whatsapp", headers=h, json={"phone_number_id": PHONE_ID, "access_token": TOKEN})
        assert r.status_code == 200, r.text
        assert (r.json()["connected"], r.json()["display_phone_number"], r.json()["webhook_ready"]) == (True, "+1 555-000-1111", True)
        assert r.json()["webhook_url"] == "https://example.ngrok.app/webhooks/whatsapp"
        assert TOKEN not in r.text
        row = await db_tx.fetchrow("select external_id, credentials from store_channels where store_id = $1 and channel = 'whatsapp'", store)
        assert row["external_id"] == PHONE_ID and TOKEN not in row["credentials"]
        assert decrypt_json(SETTINGS, row["credentials"])["access_token"] == TOKEN
        audit = await db_tx.fetchval("select after::text from audit_logs where store_id = $1 and action = 'connect_whatsapp'", store)
        assert audit is not None and TOKEN not in audit                       # the token never reaches the log

        graph.phone_status = 400
        r = await c.post("/api/channels/whatsapp", headers=h, json={"phone_number_id": PHONE_ID, "access_token": TOKEN})
        assert r.status_code == 400

        r = await c.delete("/api/channels/whatsapp", headers=h)
        assert r.json()["connected"] is False
