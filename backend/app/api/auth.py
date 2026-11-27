"""Supabase JWT verification and the dependencies routes use to get the caller's store.

store_id always comes from the verified token -> stores.owner_id lookup,
never from the request body (CLAUDE.md hard rule 3).
"""

from functools import lru_cache
from uuid import UUID

import asyncpg
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.db.pool import get_db
from app.db.stores import get_store_id_for_owner

ASYMMETRIC_ALGS = ["ES256", "RS256"]
AUDIENCE = "authenticated"

bearer = HTTPBearer(auto_error=False)


class CurrentUser(BaseModel):
    user_id: UUID
    email: str | None = None


def _unauthorized(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


@lru_cache
def _jwks_client(jwks_url: str) -> jwt.PyJWKClient:
    # Caches the signing keys; refetches when a token has an unknown kid.
    return jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=3600, timeout=10)


async def _signing_key(token: str, settings: Settings) -> tuple[object, list[str]]:
    """Pick the key based on the token's alg: JWKS for ES256/RS256, shared secret for HS256."""
    try:
        alg = jwt.get_unverified_header(token).get("alg")
    except jwt.InvalidTokenError:
        raise _unauthorized("Invalid token")

    if alg in ASYMMETRIC_ALGS:
        try:
            # PyJWKClient does blocking HTTP on a cache miss, so keep it off the event loop.
            key = await run_in_threadpool(_jwks_client(settings.jwks_url).get_signing_key_from_jwt, token)
        except jwt.PyJWKClientError:
            raise _unauthorized("Invalid token")
        return key.key, ASYMMETRIC_ALGS

    if alg == "HS256" and settings.supabase_jwt_secret:
        return settings.supabase_jwt_secret.get_secret_value(), ["HS256"]

    raise _unauthorized("Invalid token")


async def verify_token(token: str, settings: Settings) -> CurrentUser:
    key, algorithms = await _signing_key(token, settings)
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=algorithms,
            audience=AUDIENCE,
            issuer=settings.supabase_issuer,
            options={"require": ["exp", "sub", "aud"]},
        )
        return CurrentUser(user_id=UUID(claims["sub"]), email=claims.get("email"))
    except jwt.ExpiredSignatureError:
        raise _unauthorized("Token expired")
    except (jwt.InvalidTokenError, ValueError):
        raise _unauthorized("Invalid token")


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    settings: Settings = Depends(get_settings),
) -> CurrentUser:
    """401 unless the request carries a valid Supabase access token."""
    if creds is None or creds.scheme.lower() != "bearer":
        raise _unauthorized()
    return await verify_token(creds.credentials, settings)


async def is_platform_admin(conn: asyncpg.Connection, user_id: UUID) -> bool:
    return bool(await conn.fetchval("select exists(select 1 from platform_admins where user_id = $1)", user_id))


async def require_admin(
    user: CurrentUser = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
) -> CurrentUser:
    """Platform admin only (task 5.2): 403 for everyone else, store owners included.

    Admin routes are the only ones allowed to read across stores (CLAUDE.md hard rule 3).
    """
    if not await is_platform_admin(conn, user.user_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="هاي الصفحة لإدارة المنصة بس")
    return user


async def get_current_store_id(
    user: CurrentUser = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
) -> UUID:
    """The caller's store_id. 403 if the user hasn't created a store yet (task 1.1)."""
    store_id = await get_store_id_for_owner(conn, user.user_id)
    if store_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No store for this user yet")
    return store_id
