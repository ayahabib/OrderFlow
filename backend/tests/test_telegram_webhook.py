"""Telegram channel end to end with a fake Telegram API (TASKS.md 1.13).

Done-when: the same order conversation works from Telegram.
"""

import json

import httpx
import pytest
from cryptography.fernet import Fernet

from app.channels import telegram as tg
from app.channels import telegram_api
from app.utils.crypto import decrypt_json, encrypt_json
from tests.authutil import ScriptedLLM, analysis, bearer, client_for, make_token, patch_jwks
from tests.authutil import test_settings as make_settings
from tests.test_order_flow import item, new_store

pytestmark = pytest.mark.db

BASE = dict(telegram_webhook_secret="webhook-test-secret", channel_encryption_key=Fernet.generate_key().decode(),
            public_base_url="https://example.ngrok.app")
SETTINGS = make_settings(**BASE)
TOKEN = "123456789:" + "A" * 35
BOT_ID = 123456789
CHAT = 555000111


class FakeTelegram:
    """Records every Bot API call; answers like Telegram would."""

    def __init__(self, get_me_error: int | None = None, set_webhook_ok: bool = True):
        self.calls: list[tuple[str, dict]] = []
        self.get_me_error = get_me_error
        self.set_webhook_ok = set_webhook_ok
        self.webhook: dict = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[1]
        payload = json.loads(request.content or b"{}")
        self.calls.append((method, payload))
        if method == "getMe":
            if self.get_me_error:
                return httpx.Response(self.get_me_error, json={"ok": False, "error_code": self.get_me_error, "description": "Unauthorized"})
            return httpx.Response(200, json={"ok": True, "result": {"id": BOT_ID, "username": "my_store_bot"}})
        if method == "setWebhook":
            if not self.set_webhook_ok:
                return httpx.Response(400, json={"ok": False, "error_code": 400, "description": "bad webhook"})
            self.webhook = payload
        if method == "getWebhookInfo":
            return httpx.Response(200, json={"ok": True, "result": {"url": self.webhook.get("url", ""), "pending_update_count": 0}})
        return httpx.Response(200, json={"ok": True, "result": True})

    def sent(self, method="sendMessage") -> list[dict]:
        return [p for m, p in self.calls if m == method]


@pytest.fixture
def telegram(monkeypatch):
    fake = FakeTelegram()
    monkeypatch.setattr(telegram_api, "make_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)))
    return fake


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch):
    patch_jwks(monkeypatch)


async def owner_headers(conn, store):
    owner = await conn.fetchval("select owner_id from stores where id = $1", store)
    return bearer(make_token(owner))


async def connected_channel(conn, store):
    creds = encrypt_json(SETTINGS, {"token": TOKEN, "username": "my_store_bot"})
    return await conn.fetchval(
        "insert into store_channels (store_id, channel, external_id, credentials) values ($1, 'telegram', $2, $3) returning id",
        store, str(BOT_ID), creds)


class TelegramChat:
    """Plays the customer: posts updates to our webhook like Telegram does."""

    def __init__(self, client, channel_id):
        self.c, self.url, self.n = client, f"/webhooks/telegram/{channel_id}", 0
        self.headers = {"X-Telegram-Bot-Api-Secret-Token": tg.webhook_secret(SETTINGS, channel_id)}

    async def post(self, update: dict, headers=None) -> httpx.Response:
        return await self.c.post(self.url, json=update, headers=self.headers if headers is None else headers)

    async def say(self, text: str) -> httpx.Response:
        self.n += 1
        return await self.post({"update_id": self.n, "message": {
            "message_id": self.n, "text": text, "chat": {"id": CHAT, "type": "private"},
            "from": {"id": CHAT, "first_name": "سارة"}}})

    async def tap(self, title: str) -> httpx.Response:
        self.n += 1
        return await self.post({"update_id": self.n, "callback_query": {
            "id": f"cb{self.n}", "data": title, "from": {"id": CHAT, "first_name": "سارة"},
            "message": {"message_id": 1, "chat": {"id": CHAT, "type": "private"}}}})


async def test_done_when_full_order_from_telegram(db_tx, telegram):
    store = await new_store(db_tx)
    channel = await connected_channel(db_tx, store)
    llm = ScriptedLLM({
        "بدي فستانين": analysis(items=[item("فستان", qty=2)]),
        "نابلس، شارع فيصل": analysis(address_text="نابلس، شارع فيصل"),
    })
    async with client_for(db_tx, settings=SETTINGS, llm=llm) as c:
        chat = TelegramChat(c, channel)
        for text in ("بدي فستانين", "أحمر M", "نابلس، شارع فيصل", "0599123456"):
            assert (await chat.say(text)).status_code == 200
        summary = telegram.sent()[-1]
        assert summary["chat_id"] == CHAT and "الإجمالي: 250.00 ILS" in summary["text"]
        titles = [b["text"] for b in summary["reply_markup"]["inline_keyboard"][0]]
        assert titles == ["تأكيد", "تعديل", "إلغاء"]

        assert (await chat.tap("تأكيد")).status_code == 200          # the inline button

    assert telegram.sent()[-1]["text"].startswith("تم تأكيد طلبك ✓")
    assert telegram.sent("answerCallbackQuery") == [{"callback_query_id": f"cb{chat.n}"}]
    order = await db_tx.fetchrow(
        "select o.total, cu.channel::text as channel, cu.external_id, cu.name from orders o "
        "join customers cu on cu.id = o.customer_id where o.store_id = $1", store)
    assert (str(order["total"]), order["channel"], order["external_id"], order["name"]) == ("250.00", "telegram", str(CHAT), "سارة")


async def test_rejects_missing_or_wrong_secret(db_tx, telegram):
    store = await new_store(db_tx)
    channel = await connected_channel(db_tx, store)
    other_channel_secret = tg.webhook_secret(SETTINGS, store)  # a valid secret, but for another id
    async with client_for(db_tx, settings=SETTINGS) as c:
        chat = TelegramChat(c, channel)
        for headers in ({}, {"X-Telegram-Bot-Api-Secret-Token": "wrong"},
                        {"X-Telegram-Bot-Api-Secret-Token": other_channel_secret}):
            r = await chat.c.post(chat.url, json={"update_id": 1}, headers=headers)
            assert r.status_code == 403
    assert await db_tx.fetchval("select count(*) from messages where store_id = $1", store) == 0


async def test_duplicate_update_is_stored_once(db_tx, telegram):
    store = await new_store(db_tx)
    channel = await connected_channel(db_tx, store)
    async with client_for(db_tx, settings=SETTINGS, llm=ScriptedLLM()) as c:
        chat = TelegramChat(c, channel)
        update = {"update_id": 9, "message": {"message_id": 9, "text": "مرحبا", "chat": {"id": CHAT, "type": "private"}}}
        assert (await chat.post(update)).status_code == 200
        assert (await chat.post(update)).status_code == 200            # Telegram retried
    assert await db_tx.fetchval("select count(*) from messages where store_id = $1 and sender = 'customer'", store) == 1
    assert len(telegram.sent()) == 1


async def test_groups_ignored_and_photos_answered(db_tx, telegram):
    store = await new_store(db_tx)
    channel = await connected_channel(db_tx, store)
    async with client_for(db_tx, settings=SETTINGS) as c:
        chat = TelegramChat(c, channel)
        await chat.post({"update_id": 1, "message": {"message_id": 1, "text": "هاي", "chat": {"id": -5, "type": "group"}}})
        await chat.post({"update_id": 2, "message": {"message_id": 2, "photo": [{"file_id": "x"}],
                                                       "chat": {"id": CHAT, "type": "private"}}})
    assert await db_tx.fetchval("select count(*) from messages where store_id = $1", store) == 0
    assert [m["text"] for m in telegram.sent()] == [tg.UNSUPPORTED_REPLY]


async def test_disconnected_channel_drops_updates(db_tx, telegram):
    store = await new_store(db_tx)
    channel = await connected_channel(db_tx, store)
    await db_tx.execute("update store_channels set is_active = false, credentials = null where id = $1", channel)
    async with client_for(db_tx, settings=SETTINGS) as c:
        assert (await TelegramChat(c, channel).say("مرحبا")).status_code == 200
    assert await db_tx.fetchval("select count(*) from messages where store_id = $1", store) == 0


async def test_owner_reply_reaches_telegram_customer(db_tx, telegram):
    store = await new_store(db_tx)
    channel = await connected_channel(db_tx, store)
    async with client_for(db_tx, settings=SETTINGS, llm=ScriptedLLM()) as c:
        await TelegramChat(c, channel).say("بدي احكي مع حدا")          # escalates, bot stops
        conv = await db_tx.fetchval("select id from conversations where store_id = $1", store)
        r = await c.post(f"/api/conversations/{conv}/reply", headers=await owner_headers(db_tx, store),
                         json={"text": "أهلاً، أنا صاحب المحل"})
    assert r.status_code == 200 and r.json()["can_reply"] is True
    assert telegram.sent()[-1] == {"chat_id": CHAT, "text": "أهلاً، أنا صاحب المحل", "disable_web_page_preview": True}


# --- connect API ------------------------------------------------------------------------

async def test_connect_registers_webhook_and_encrypts_token(db_tx, telegram):
    store = await new_store(db_tx)
    h = await owner_headers(db_tx, store)
    async with client_for(db_tx, settings=SETTINGS) as c:
        r = await c.post("/api/channels/telegram", headers=h, json={"bot_token": TOKEN})
        assert r.status_code == 200, r.text
        assert r.json()["connected"] and r.json()["bot_username"] == "my_store_bot"

        row = await db_tx.fetchrow("select id, external_id, credentials, is_active from store_channels where store_id = $1", store)
        assert row["external_id"] == str(BOT_ID) and row["is_active"]
        assert TOKEN not in row["credentials"] and decrypt_json(SETTINGS, row["credentials"])["token"] == TOKEN
        assert telegram.webhook["url"] == f"https://example.ngrok.app/webhooks/telegram/{row['id']}"
        assert telegram.webhook["secret_token"] == tg.webhook_secret(SETTINGS, row["id"])

        status = (await c.get("/api/channels/telegram", headers=h)).json()
        assert status == {"connected": True, "bot_username": "my_store_bot", "webhook_ok": True, "webhook_error": None}

        r = await c.delete("/api/channels/telegram", headers=h)
        assert r.json()["connected"] is False
    assert ("deleteWebhook", {}) in telegram.calls
    row = await db_tx.fetchrow("select credentials, is_active from store_channels where store_id = $1", store)
    assert row["is_active"] is False and row["credentials"] is None


@pytest.mark.parametrize("settings_over, token, code, fragment", [
    ({"public_base_url": "http://localhost:8000"}, TOKEN, 400, "https"),
    ({}, "not-a-token", 422, "التوكن"),
    ({"telegram_notify_bot_token": TOKEN}, TOKEN, 400, "الإشعارات"),
])
async def test_connect_refuses_bad_setup(db_tx, telegram, settings_over, token, code, fragment):
    store = await new_store(db_tx)
    settings = make_settings(**{**BASE, **settings_over})
    async with client_for(db_tx, settings=settings) as c:
        r = await c.post("/api/channels/telegram", headers=await owner_headers(db_tx, store), json={"bot_token": token})
    assert r.status_code == code and fragment in r.json()["detail"]
    assert await db_tx.fetchval("select count(*) from store_channels where store_id = $1", store) == 0


async def test_connect_wrong_token_or_webhook_refused_saves_nothing(db_tx, telegram):
    store = await new_store(db_tx)
    h = await owner_headers(db_tx, store)
    async with client_for(db_tx, settings=SETTINGS) as c:
        telegram.get_me_error = 401
        r = await c.post("/api/channels/telegram", headers=h, json={"bot_token": TOKEN})
        assert r.status_code == 400 and "غلط" in r.json()["detail"]

        telegram.get_me_error, telegram.set_webhook_ok = None, False
        r = await c.post("/api/channels/telegram", headers=h, json={"bot_token": TOKEN})
        assert r.status_code == 502
    assert await db_tx.fetchval("select count(*) from store_channels where store_id = $1", store) == 0


async def test_bot_of_another_store_is_refused(db_tx, telegram):
    other = await new_store(db_tx)
    await connected_channel(db_tx, other)
    mine = await new_store(db_tx)
    async with client_for(db_tx, settings=SETTINGS) as c:
        r = await c.post("/api/channels/telegram", headers=await owner_headers(db_tx, mine), json={"bot_token": TOKEN})
    assert r.status_code == 409
    assert telegram.webhook == {}                                     # the other store's webhook untouched
