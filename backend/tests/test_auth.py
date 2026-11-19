"""Auth: Supabase JWT verification, GET /api/me, and get_current_store_id."""

import time
import uuid
from types import SimpleNamespace

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import Depends

from app.api import auth
from app.api.auth import get_current_store_id
from app.config import Settings, get_settings
from app.db.pool import get_db
from app.main import create_app

TEST_URL = "https://test-project.supabase.co"
TEST_ISSUER = f"{TEST_URL}/auth/v1"
HS_SECRET = "test-only-hs256-secret-at-least-32-bytes-long"
EC_KEY = ec.generate_private_key(ec.SECP256R1())
OTHER_EC_KEY = ec.generate_private_key(ec.SECP256R1())


def make_token(sub=None, *, key=EC_KEY, alg="ES256", exp_in=3600, aud="authenticated", iss=TEST_ISSUER, **extra):
    claims = {"sub": str(sub or uuid.uuid4()), "aud": aud, "iss": iss,
              "exp": int(time.time()) + exp_in, "role": "authenticated", **extra}
    return jwt.encode(claims, key, algorithm=alg, headers={"kid": "test-kid"})


class FakeConn:
    """Stands in for asyncpg.Connection: returns a fixed store_id for fetchval."""

    def __init__(self, store_id):
        self.store_id = store_id

    async def fetchval(self, query, *args):
        return self.store_id


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch):
    """JWKS lookups return our test EC public key instead of calling Supabase."""
    client = SimpleNamespace(get_signing_key_from_jwt=lambda token: SimpleNamespace(key=EC_KEY.public_key()))
    monkeypatch.setattr(auth, "_jwks_client", lambda url: client)


def build_app(conn):
    app = create_app(use_lifespan=False)
    app.dependency_overrides[get_settings] = lambda: Settings(
        supabase_url=TEST_URL, database_url="postgresql://unused", supabase_jwt_secret=HS_SECRET
    )

    async def override_db():
        yield conn

    app.dependency_overrides[get_db] = override_db

    # A test-only route protected by get_current_store_id.
    @app.get("/api/_store_probe")
    async def probe(store_id: uuid.UUID = Depends(get_current_store_id)):
        return {"store_id": str(store_id)}

    return app


def client_for(conn):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=build_app(conn)), base_url="http://test")


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


# --- 401 cases -------------------------------------------------------------

async def test_me_without_token_is_401():
    async with client_for(FakeConn(None)) as c:
        r = await c.get("/api/me")
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("token", [
    "not-a-jwt",
    make_token(exp_in=-60),                       # expired
    make_token(aud="anon"),                       # wrong audience
    make_token(iss="https://evil.example/auth/v1"),  # wrong issuer
    make_token(key=OTHER_EC_KEY),                 # signed by someone else's key
    make_token(key="wrong-secret-also-32-bytes-long!!", alg="HS256"),
])
async def test_me_rejects_bad_tokens(token):
    async with client_for(FakeConn(None)) as c:
        r = await c.get("/api/me", headers=bearer(token))
    assert r.status_code == 401


async def test_hs256_rejected_when_no_secret_configured():
    app = build_app(FakeConn(None))
    app.dependency_overrides[get_settings] = lambda: Settings(
        supabase_url=TEST_URL, database_url="postgresql://unused", supabase_jwt_secret=None
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/me", headers=bearer(make_token(key=HS_SECRET, alg="HS256")))
    assert r.status_code == 401


# --- success cases ---------------------------------------------------------

async def test_me_returns_store_id_es256():
    user_id, store_id = uuid.uuid4(), uuid.uuid4()
    async with client_for(FakeConn(store_id)) as c:
        r = await c.get("/api/me", headers=bearer(make_token(user_id, email="owner@example.test")))
    assert r.status_code == 200
    assert r.json() == {"user_id": str(user_id), "email": "owner@example.test", "store_id": str(store_id)}


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
    user_id = uuid.uuid4()
    await db_tx.execute(
        "insert into auth.users (id, email, aud, role) values ($1, $2, 'authenticated', 'authenticated')",
        user_id, f"me-test-{user_id.hex[:8]}@example.test",
    )
    store_id = await db_tx.fetchval(
        "insert into stores (owner_id, name) values ($1, 'متجر الاختبار') returning id", user_id
    )
    async with client_for(db_tx) as c:
        r = await c.get("/api/me", headers=bearer(make_token(user_id)))
        probe = await c.get("/api/_store_probe", headers=bearer(make_token(user_id)))
    assert r.status_code == 200
    assert r.json()["store_id"] == str(store_id)
    assert probe.json() == {"store_id": str(store_id)}
