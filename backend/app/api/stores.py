"""The logged-in owner's store: create (once), read, and edit settings.

There is no store_id in any path or body: the store is always the caller's own,
resolved from the verified token (CLAUDE.md hard rule 3).
"""

import re
from datetime import datetime
from typing import Annotated
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import AfterValidator, BaseModel, ConfigDict, StringConstraints

from app.api.auth import CurrentUser, get_current_store_id, get_current_user
from app.db import stores as db
from app.db.pool import get_db

router = APIRouter(prefix="/api/store", tags=["store"])

PHONE_RE = re.compile(r"^\+?[0-9][0-9 \-]{5,19}$")


def _check_phone(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    if not PHONE_RE.fullmatch(value):
        raise ValueError("رقم الهاتف غير صالح")
    return value


StoreName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Phone = Annotated[
    str | None, StringConstraints(strip_whitespace=True, max_length=20), AfterValidator(_check_phone)
]
def _check_currency(value: str) -> str:
    value = value.strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", value):
        raise ValueError("رمز العملة لازم يكون 3 حروف مثل ILS")
    return value


# One currency per store (PRD decision 7), ISO 4217 code such as ILS, JOD, USD.
Currency = Annotated[str, AfterValidator(_check_currency)]


class StoreCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")  # rejects store_id / owner_id in the body
    name: StoreName
    phone: Phone = None
    currency: Currency = "ILS"


class StoreUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: StoreName | None = None
    phone: Phone = None
    currency: Currency | None = None


class StoreOut(BaseModel):
    id: UUID
    name: str
    phone: str | None
    currency: str
    timezone: str
    created_at: datetime


@router.post("", response_model=StoreOut, status_code=status.HTTP_201_CREATED)
async def create_my_store(
    body: StoreCreate,
    user: CurrentUser = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
) -> StoreOut:
    row = await db.create_store(conn, user.user_id, body.name, body.phone, body.currency)
    if row is None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="لديك متجر بالفعل")
    return StoreOut(**dict(row))


@router.get("", response_model=StoreOut)
async def get_my_store(
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> StoreOut:
    row = await db.get_store(conn, store_id)
    return StoreOut(**dict(row))


@router.patch("", response_model=StoreOut)
async def update_my_store(
    body: StoreUpdate,
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> StoreOut:
    changes = body.model_dump(exclude_unset=True)
    # name and currency are NOT NULL: an explicit null means "no change" is not allowed.
    for required in ("name", "currency"):
        if required in changes and changes[required] is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{required} لا يمكن أن يكون فارغاً")
    row = await db.update_store(conn, store_id, changes)
    return StoreOut(**dict(row))
