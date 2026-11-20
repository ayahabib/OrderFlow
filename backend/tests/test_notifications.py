"""Owner Telegram alerts and the link code (TASKS.md 1.11). No real Telegram calls."""

import json
import uuid

import httpx
import pytest

from app.services.notifications import (
    LINK_TTL_SECONDS, NotifyError, TelegramNotifier, build_alert, find_link, make_link_code, read_link_code,
)

SECRET = b"secret"
NOW = 1_800_000_000


def test_link_code_round_trip():
    store = uuid.uuid4()
    code = make_link_code(SECRET, store, NOW)
    assert len(code) <= 64 and all(ch.isalnum() or ch in "-_" for ch in code)  # Telegram /start limits
    assert read_link_code(SECRET, code, NOW + 10) == store


def test_link_code_rejects_tampering_expiry_and_other_keys():
    store = uuid.uuid4()
    code = make_link_code(SECRET, store, NOW)
    assert read_link_code(SECRET, code, NOW + LINK_TTL_SECONDS + 1) is None
    assert read_link_code(b"other", code, NOW) is None
    flipped = code[:5] + ("A" if code[5] != "A" else "B") + code[6:]
    assert read_link_code(SECRET, flipped, NOW) is None
    assert read_link_code(SECRET, "not-a-code", NOW) is None
    assert read_link_code(SECRET, "", NOW) is None


def test_find_link_only_matches_this_store_and_takes_latest():
    mine, other = uuid.uuid4(), uuid.uuid4()
    payloads = [(111, make_link_code(SECRET, mine, NOW)), (222, make_link_code(SECRET, other, NOW)),
                (333, "garbage"), (444, make_link_code(SECRET, mine, NOW))]
    assert find_link(SECRET, mine, payloads, NOW) == 444
    assert find_link(SECRET, uuid.uuid4(), payloads, NOW) is None


def test_build_alert_has_reason_history_and_link():
    text = build_alert("متجري", "complaint", "القطعة مكسورة",
                       [("customer", "مرحبا"), ("bot", "أهلاً"), ("customer", "القطعة مكسورة")],
                       "http://dash/conversations/1")
    assert "متجري" in text and "السبب: شكوى" in text
    assert "العميل: مرحبا" in text and "البوت: أهلاً" in text
    assert text.endswith("http://dash/conversations/1")


def test_build_alert_fits_telegram_limit():
    history = [("customer", "كلام " * 200)] * 30
    text = build_alert("متجر", None, "سؤال", history, "http://x")
    assert len(text) <= 4096
    assert "تحويل لصاحب المتجر" in text and text.endswith("http://x")


def bot_with(handler) -> TelegramNotifier:
    return TelegramNotifier("123:SECRET-TOKEN", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_send_posts_plain_text():
    seen = []

    def handler(request):
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"ok": True, "result": {}})

    await bot_with(handler).send(42, "مرحبا")
    assert seen == [("/bot123:SECRET-TOKEN/sendMessage", {"chat_id": 42, "text": "مرحبا", "disable_web_page_preview": True})]


async def test_start_payloads_reads_private_start_messages_only():
    updates = [
        {"message": {"chat": {"id": 1, "type": "private"}, "text": "/start CODE1"}},
        {"message": {"chat": {"id": 2, "type": "group"}, "text": "/start CODE2"}},
        {"message": {"chat": {"id": 3, "type": "private"}, "text": "hello"}},
        {"edited_message": {}},
    ]
    bot = bot_with(lambda r: httpx.Response(200, json={"ok": True, "result": updates}))
    assert await bot.start_payloads() == [(1, "CODE1")]


async def test_errors_never_leak_the_token():
    bot = bot_with(lambda r: httpx.Response(403, json={"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked"}))
    with pytest.raises(NotifyError) as exc:
        await bot.send(1, "x")
    assert "403" in str(exc.value) and "SECRET-TOKEN" not in str(exc.value)
    assert exc.value.error_code == 403

    def boom(request):
        raise httpx.ConnectError(f"cannot reach {request.url}")

    with pytest.raises(NotifyError) as exc:
        await bot_with(boom).send(1, "x")
    assert "SECRET-TOKEN" not in str(exc.value)
