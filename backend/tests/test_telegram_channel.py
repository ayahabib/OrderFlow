"""Telegram customer channel, pure parts (TASKS.md 1.13): parsing, secrets, sending, encryption."""

import json
import uuid

import httpx
import pytest
from cryptography.fernet import Fernet

from app.channels import telegram as tg
from app.channels import telegram_api
from app.channels.base import Button, OutboundMessage
from app.utils.crypto import CryptoError, decrypt_json, encrypt_json
from tests.authutil import test_settings as make_settings


def private(text=None, message_id=7, chat_id=555, **extra):
    msg = {"message_id": message_id, "chat": {"id": chat_id, "type": "private"},
           "from": {"id": chat_id, "first_name": "سارة", "last_name": "أحمد"}, **extra}
    if text is not None:
        msg["text"] = text
    return {"update_id": 1, "message": msg}


def test_parse_text_message():
    p = tg.parse_update(private("بدي فستان"))
    assert (p.chat_id, p.customer_name, p.text, p.external_message_id) == ("555", "سارة أحمد", "بدي فستان", "tg:555:7")
    assert p.callback_query_id is None


def test_parse_button_press_as_its_title():
    update = {"update_id": 2, "callback_query": {
        "id": "cb1", "data": "تأكيد", "from": {"id": 555, "first_name": "سارة"},
        "message": {"message_id": 9, "chat": {"id": 555, "type": "private"}}}}
    p = tg.parse_update(update)
    assert (p.text, p.callback_query_id, p.external_message_id) == ("تأكيد", "cb1", "tg:cb:cb1")


def test_parse_ignores_groups_edits_and_reports_unreadable():
    group = {"update_id": 3, "message": {"message_id": 1, "chat": {"id": -10, "type": "group"}, "text": "هاي"}}
    assert tg.parse_update(group) is None
    assert tg.parse_update({"update_id": 4, "edited_message": {}}) is None
    photo = tg.parse_update(private(photo=[{"file_id": "x"}]))
    assert photo is not None and photo.text is None          # we answer "text only"


def test_webhook_secret_per_channel():
    settings = make_settings(telegram_webhook_secret="s3cret")
    a, b = uuid.uuid4(), uuid.uuid4()
    assert tg.webhook_secret(settings, a) == tg.webhook_secret(settings, a)
    assert tg.webhook_secret(settings, a) != tg.webhook_secret(settings, b)
    assert all(ch in "0123456789abcdef" for ch in tg.webhook_secret(settings, a))
    with pytest.raises(tg.ChannelNotConfigured):
        tg.webhook_secret(make_settings(telegram_webhook_secret=None), a)


def test_credentials_round_trip_and_wrong_key():
    settings = make_settings(channel_encryption_key=Fernet.generate_key().decode())
    blob = encrypt_json(settings, {"token": "123:abc"})
    assert "123:abc" not in blob
    assert decrypt_json(settings, blob) == {"token": "123:abc"}
    with pytest.raises(CryptoError):
        decrypt_json(make_settings(channel_encryption_key=Fernet.generate_key().decode()), blob)
    with pytest.raises(CryptoError):
        encrypt_json(make_settings(channel_encryption_key=None), {})


async def test_adapter_sends_inline_buttons(monkeypatch):
    sent = []

    def handler(request):
        sent.append((request.url.path.rsplit("/", 1)[1], json.loads(request.content)))
        return httpx.Response(200, json={"ok": True, "result": {}})

    monkeypatch.setattr(telegram_api, "make_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    adapter = tg.TelegramAdapter("123:TOKEN")
    await adapter.send("555", OutboundMessage(text="ملخص", buttons=[Button(id="confirm", title="تأكيد"),
                                                                    Button(id="cancel", title="إلغاء")]))
    await adapter.answer_button("cb1")
    (method, payload), (method2, payload2) = sent
    assert method == "sendMessage" and payload["chat_id"] == 555 and payload["text"] == "ملخص"
    assert payload["reply_markup"] == {"inline_keyboard": [[{"text": "تأكيد", "callback_data": "تأكيد"},
                                                             {"text": "إلغاء", "callback_data": "إلغاء"}]]}
    assert (method2, payload2) == ("answerCallbackQuery", {"callback_query_id": "cb1"})


def test_callback_data_fits_telegram_limit():
    assert len(tg._callback_data("ا" * 100).encode()) <= 64
