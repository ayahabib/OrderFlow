"""GET /api/me: who is logged in, which store they own, and whether they are a platform admin."""

from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.auth import CurrentUser, get_current_user, is_platform_admin
from app.db.pool import get_db
from app.db.stores import get_store_id_for_owner

router = APIRouter(prefix="/api", tags=["auth"])


class MeResponse(BaseModel):
    user_id: UUID
    email: str | None
    store_id: UUID | None  # None until the store is created (task 1.1)
    is_admin: bool = False  # platform admin (task 5.2): the frontend shows the admin pages


@router.get("/me", response_model=MeResponse)
async def me(
    user: CurrentUser = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
) -> MeResponse:
    store_id = await get_store_id_for_owner(conn, user.user_id)
    return MeResponse(user_id=user.user_id, email=user.email, store_id=store_id,
                      is_admin=await is_platform_admin(conn, user.user_id))
