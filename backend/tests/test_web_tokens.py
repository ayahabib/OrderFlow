"""Web chat session tokens (no database)."""

import uuid

from app.channels import web

SECRET = "s3cret"
STORE, OTHER_STORE = uuid.uuid4(), uuid.uuid4()


def test_valid_token_returns_session():
    sid = web.new_session_id()
    assert web.verify_token(SECRET, STORE, web.make_token(SECRET, STORE, sid)) == sid


def test_token_is_bound_to_store_and_secret():
    token = web.make_token(SECRET, STORE, web.new_session_id())
    assert web.verify_token(SECRET, OTHER_STORE, token) is None
    assert web.verify_token("other-secret", STORE, token) is None


def test_tampered_or_malformed_tokens_are_rejected():
    sid = web.new_session_id()
    token = web.make_token(SECRET, STORE, sid)
    forged_sid = web.new_session_id()
    signature = token.rsplit(".", 1)[1]
    for bad in (None, "", "no-dot", f"{forged_sid}.{signature}", token[:-2] + "xx",
                "x." + signature, f"{'sim-' + 'a' * 80}.{signature}"):
        assert web.verify_token(SECRET, STORE, bad) is None


def test_external_message_id_is_namespaced():
    assert web.external_message_id("sim-abc", "m1") == "web:sim-abc:m1"
