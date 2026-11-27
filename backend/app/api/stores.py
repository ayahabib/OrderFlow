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
from app.services import audit
from app.utils.phone import COUNTRY_CODE

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


def _check_country_code(value: str) -> str:
    value = value.strip().lstrip("+")
    if not COUNTRY_CODE.fullmatch(value):
        raise ValueError("مفتاح الدولة لازم يكون أرقام بس، مثل 970")
    return value


# Reads customers' local numbers (0599... -> +970599...) on the customers page (task 3.9).
CountryCode = Annotated[str, AfterValidator(_check_country_code)]


class StoreCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")  # rejects store_id / owner_id in the body
    name: StoreName
    phone: Phone = None
    currency: Currency = "ILS"
    phone_country_code: CountryCode = "970"


class StoreUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: StoreName | None = None
    phone: Phone = None
    currency: Currency | None = None
    phone_country_code: CountryCode | None = None
    bot_enabled: bool | None = None   # store-wide auto-reply switch (task 5.5)


class StoreOut(BaseModel):
    id: UUID
    name: str
    phone: str | None
    currency: str
    phone_country_code: str
    timezone: str
    bot_enabled: bool
    suspended_at: datetime | None = None   # set by the platform admin (5.2): the bot answers nobody
    created_at: datetime


@router.post("", response_model=StoreOut, status_code=status.HTTP_201_CREATED)
async def create_my_store(
    body: StoreCreate,
    user: CurrentUser = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
) -> StoreOut:
    row = await db.create_store(conn, user.user_id, body.name, body.phone, body.currency, body.phone_country_code)
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
    # These are NOT NULL: an explicit null is not allowed.
    for required in ("name", "currency", "phone_country_code", "bot_enabled"):
        if required in changes and changes[required] is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{required} لا يمكن أن يكون فارغاً")
    old = await db.get_store(conn, store_id)
    if changes.get("bot_enabled") is True and old["suspended_at"] is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail="المتجر موقوف من إدارة المنصة، فما بينفع تشغّل الرد الآلي هلق.")
    row = await db.update_store(conn, store_id, changes)
    fields = [k for k in changes if k in db.EDITABLE_COLUMNS]
    if fields:
        await audit.log(conn, store_id, "owner", "update_my_store", store_id,
                        before={k: old[k] for k in fields}, after={k: row[k] for k in fields})   # 3.14
    return StoreOut(**dict(row))
