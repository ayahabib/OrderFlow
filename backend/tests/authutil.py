"""Shared test helpers: sign test tokens and build an app wired to a given connection."""

import time
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import Depends

from app.api import auth
from app.api.auth import get_current_store_id
from app.config import Settings, get_settings
from app.db.pool import get_db, get_db_factory
from app.main import create_app

TEST_URL = "https://test-project.supabase.co"
TEST_ISSUER = f"{TEST_URL}/auth/v1"
HS_SECRET = "test-only-hs256-secret-at-least-32-bytes-long"
WEB_SECRET = "test-only-web-chat-secret"
EC_KEY = ec.generate_private_key(ec.SECP256R1())
OTHER_EC_KEY = ec.generate_private_key(ec.SECP256R1())


def test_settings(**overrides) -> Settings:
    values = dict(supabase_url=TEST_URL, database_url="postgresql://unused", supabase_jwt_secret=HS_SECRET,
                  web_chat_secret=WEB_SECRET)
    values.update(overrides)
    return Settings(**values)


def make_token(sub=None, *, key=EC_KEY, alg="ES256", exp_in=3600, aud="authenticated", iss=TEST_ISSUER, **extra):
    claims = {"sub": str(sub or uuid.uuid4()), "aud": aud, "iss": iss,
              "exp": int(time.time()) + exp_in, "role": "authenticated", **extra}
    return jwt.encode(claims, key, algorithm=alg, headers={"kid": "test-kid"})


def bearer(token) -> dict:
    return {"Authorization": f"Bearer {token}"}


def patch_jwks(monkeypatch) -> None:
    """JWKS lookups return the test EC public key instead of calling Supabase."""
    client = SimpleNamespace(get_signing_key_from_jwt=lambda token: SimpleNamespace(key=EC_KEY.public_key()))
    monkeypatch.setattr(auth, "_jwks_client", lambda url: client)


def build_app(conn, settings: Settings | None = None):
    app = create_app(use_lifespan=False)
    app.dependency_overrides[get_settings] = lambda: settings or test_settings()

    async def override_db():
        yield conn

    app.dependency_overrides[get_db] = override_db

    # Background tasks borrow "another" connection: in tests it is the same rolled-back one.
    @asynccontextmanager
    async def same_conn():
        yield conn

    app.dependency_overrides[get_db_factory] = lambda: same_conn

    # A test-only route protected by get_current_store_id.
    @app.get("/api/_store_probe")
    async def probe(store_id: uuid.UUID = Depends(get_current_store_id)):
        return {"store_id": str(store_id)}

    return app


def client_for(conn, settings: Settings | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=build_app(conn, settings)), base_url="http://test")


async def insert_auth_user(conn, user_id: uuid.UUID | None = None) -> uuid.UUID:
    """Insert a bare auth.users row (tests run inside a rolled-back transaction)."""
    user_id = user_id or uuid.uuid4()
    await conn.execute(
        "insert into auth.users (id, email, aud, role) values ($1, $2, 'authenticated', 'authenticated')",
        user_id, f"test-{user_id.hex[:8]}@example.test",
    )
    return user_id
