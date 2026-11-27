"""Auth: Supabase JWT verification, GET /api/me, and get_current_store_id."""

import uuid

import pytest

from tests.authutil import (
    HS_SECRET, OTHER_EC_KEY, bearer, client_for, insert_auth_user, make_token, patch_jwks, test_settings,
)


class FakeConn:
    """Stands in for asyncpg.Connection: returns a fixed store_id for fetchval (and "not an admin")."""

    def __init__(self, store_id):
        self.store_id = store_id

    async def fetchval(self, query, *args):
        if "platform_admins" in query:
            return False
        return self.store_id


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch):
    patch_jwks(monkeypatch)


# --- 401 cases -------------------------------------------------------------

async def test_me_without_token_is_401():
    async with client_for(FakeConn(None)) as c:
        r = await c.get("/api/me")
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("token", [
    "not-a-jwt",
    make_token(exp_in=-60),                          # expired
    make_token(aud="anon"),                          # wrong audience
    make_token(iss="https://evil.example/auth/v1"),  # wrong issuer
    make_token(key=OTHER_EC_KEY),                    # signed by someone else's key
    make_token(key="wrong-secret-also-32-bytes-long!!", alg="HS256"),
])
async def test_me_rejects_bad_tokens(token):
    async with client_for(FakeConn(None)) as c:
        r = await c.get("/api/me", headers=bearer(token))
    assert r.status_code == 401


async def test_hs256_rejected_when_no_secret_configured():
    async with client_for(FakeConn(None), test_settings(supabase_jwt_secret=None)) as c:
        r = await c.get("/api/me", headers=bearer(make_token(key=HS_SECRET, alg="HS256")))
    assert r.status_code == 401


# --- success cases ---------------------------------------------------------

async def test_me_returns_store_id_es256():
    user_id, store_id = uuid.uuid4(), uuid.uuid4()
    async with client_for(FakeConn(store_id)) as c:
        r = await c.get("/api/me", headers=bearer(make_token(user_id, email="owner@example.test")))
    assert r.status_code == 200
    assert r.json() == {"user_id": str(user_id), "email": "owner@example.test", "store_id": str(store_id),
                        "is_admin": False}


async def test_me_accepts_legacy_hs256():
    store_id = uuid.uuid4()
    async with client_for(FakeConn(store_id)) as c:
        r = await c.get("/api/me", headers=bearer(make_token(key=HS_SECRET, alg="HS256")))
    assert r.status_code == 200
    assert r.json()["store_id"] == str(store_id)


async def test_me_user_without_store_has_null_store_id():
    async with client_for(FakeConn(None)) as c:
        r = await c.get("/api/me", headers=bearer(make_token()))
    assert r.status_code == 200
    assert r.json()["store_id"] is None


# --- get_current_store_id --------------------------------------------------

async def test_store_dependency_403_without_store():
    async with client_for(FakeConn(None)) as c:
        r = await c.get("/api/_store_probe", headers=bearer(make_token()))
    assert r.status_code == 403


async def test_store_dependency_401_without_token():
    async with client_for(FakeConn(uuid.uuid4())) as c:
        r = await c.get("/api/_store_probe")
    assert r.status_code == 401


# --- real database lookup --------------------------------------------------

@pytest.mark.db
async def test_me_looks_up_real_store(db_tx):
    """Uses the real stores table (inside a rolled-back transaction)."""
    user_id = await insert_auth_user(db_tx)
    store_id = await db_tx.fetchval(
        "insert into stores (owner_id, name) values ($1, 'متجر الاختبار') returning id", user_id
    )
    async with client_for(db_tx) as c:
        r = await c.get("/api/me", headers=bearer(make_token(user_id)))
        probe = await c.get("/api/_store_probe", headers=bearer(make_token(user_id)))
    assert r.status_code == 200
    assert r.json()["store_id"] == str(store_id)
    assert probe.json() == {"store_id": str(store_id)}
