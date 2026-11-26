"""Delivery zones: the only source of delivery fees (the bot never invents a fee, hard rule 4)."""

from decimal import Decimal
from typing import Annotated
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.api.auth import get_current_store_id
from app.db import zones as db
from app.db.pool import get_db
from app.utils.arabic import normalize_arabic

router = APIRouter(prefix="/api/delivery-zones", tags=["delivery zones"])

ZoneName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Fee = Annotated[Decimal, Field(ge=0, max_digits=10, decimal_places=2)]


class ZoneCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")  # no store_id from the body
    name: ZoneName
    fee: Fee
    is_active: bool = True
    is_pickup: bool = False   # pickup from the store (3.15): no address, fee always 0


class ZoneUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: ZoneName | None = None
    fee: Fee | None = None
    is_active: bool | None = None
    is_pickup: bool | None = None


class ZoneOut(BaseModel):
    id: UUID
    name: str
    fee: Decimal
    is_active: bool
    is_pickup: bool


async def _ensure_one_pickup(conn, store_id: UUID) -> None:
    """Checked before writing, so the error is clear (the unique index still guards races)."""
    if any(z["is_pickup"] for z in await db.list_zones(conn, store_id)):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="في خيار استلام من المحل واحد بس")


def _conflict(exc: asyncpg.UniqueViolationError) -> str:
    if "pickup" in (exc.constraint_name or ""):
        return "في خيار استلام من المحل واحد بس"
    return "المنطقة موجودة من قبل"


def _clean_name(name: str) -> str:
    return " ".join(name.split())


async def _ensure_unique(conn, store_id: UUID, name: str, except_id: UUID | None = None) -> None:
    """Same zone with another spelling ("رام الله" / "رام اللة") counts as a duplicate."""
    key = normalize_arabic(name)
    for zone in await db.list_zones(conn, store_id):
        if zone["id"] != except_id and normalize_arabic(zone["name"]) == key:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=f"المنطقة \"{zone['name']}\" موجودة من قبل")


@router.get("", response_model=list[ZoneOut])
async def list_zones(
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> list[dict]:
    return await db.list_zones(conn, store_id)


@router.post("", response_model=ZoneOut, status_code=status.HTTP_201_CREATED)
async def create_zone(
    body: ZoneCreate,
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> dict:
    name = _clean_name(body.name)
    await _ensure_unique(conn, store_id, name)
    if body.is_pickup:
        await _ensure_one_pickup(conn, store_id)
    fee = Decimal("0") if body.is_pickup else body.fee   # pickup never costs delivery
    try:
        return await db.insert_zone(conn, store_id, name, fee, body.is_active, body.is_pickup)
    except asyncpg.UniqueViolationError as exc:  # same name in a parallel request, or a second pickup
        raise HTTPException(status.HTTP_409_CONFLICT, detail=_conflict(exc))


@router.patch("/{zone_id}", response_model=ZoneOut)
async def update_zone(
    zone_id: UUID,
    body: ZoneUpdate,
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> dict:
    changes = body.model_dump(exclude_unset=True)
    for required in ("name", "fee", "is_active", "is_pickup"):
        if required in changes and changes[required] is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{required} لا يمكن أن يكون فارغاً")
    current = await db.get_zone(conn, store_id, zone_id)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="المنطقة غير موجودة")
    if changes.get("is_pickup") and not current["is_pickup"]:
        await _ensure_one_pickup(conn, store_id)
    if changes.get("is_pickup", current["is_pickup"]):
        if changes.get("fee", 0) != 0:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="الاستلام من المحل أجرته 0 دايماً")
        changes["fee"] = Decimal("0")
    if "name" in changes:
        changes["name"] = _clean_name(changes["name"])
        await _ensure_unique(conn, store_id, changes["name"], except_id=zone_id)
    try:
        return await db.update_zone(conn, store_id, zone_id, changes)
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=_conflict(exc))


@router.delete("/{zone_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_zone(
    zone_id: UUID,
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> None:
    try:
        found = await db.delete_zone(conn, store_id, zone_id)
    except asyncpg.ForeignKeyViolationError:
        # Orders point at this zone; deleting would break their history.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="في طلبات على هالمنطقة، فما بتنحذف. أوقفها بدل الحذف.",
        )
    if not found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="المنطقة غير موجودة")
