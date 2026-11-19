"""Offers page API: list, create, edit (incl. pause/resume), delete.

Validation lives in `check_offer` so create and edit (merged with the stored row)
follow the same rules. The DB check constraints are a second safety net.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

from app.api.auth import get_current_store_id
from app.db import offers as db
from app.db.pool import get_db
from app.services.pricing import Offer, offer_status

router = APIRouter(prefix="/api/offers", tags=["offers"])

Kind = Literal["percent_off", "fixed_price", "free_delivery"]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Percent = Annotated[Decimal, Field(gt=0, lt=100, max_digits=5, decimal_places=2)]
Money = Annotated[Decimal, Field(ge=0, max_digits=10, decimal_places=2)]


def check_offer(o: dict) -> None:
    """Raise ValueError (Arabic) if the offer's fields don't fit its kind."""
    kind = o["kind"]
    has_target = (o.get("product_id") is not None) + (o.get("variant_id") is not None)
    if kind in ("percent_off", "fixed_price"):
        if has_target != 1:
            raise ValueError("اختار منتج أو نسخة وحدة للعرض")
        if kind == "percent_off" and o.get("percent") is None:
            raise ValueError("نسبة الخصم مطلوبة")
        if kind == "fixed_price" and o.get("fixed_price") is None:
            raise ValueError("سعر العرض مطلوب")
    else:
        if has_target:
            raise ValueError("التوصيل المجاني بيكون على كل الطلب، مش على منتج")
        if o.get("min_order_total") is None:
            raise ValueError("الحد الأدنى للطلب مطلوب")
    if o.get("starts_at") and o.get("ends_at") and o["ends_at"] <= o["starts_at"]:
        raise ValueError("تاريخ النهاية لازم يكون بعد البداية")


def _clean_for_kind(o: dict) -> dict:
    """Drop value fields that don't belong to the kind (the DB check requires them NULL)."""
    keep = {"percent_off": "percent", "fixed_price": "fixed_price", "free_delivery": "min_order_total"}[o["kind"]]
    return {**o, **{f: None for f in ("percent", "fixed_price", "min_order_total") if f != keep}}


class OfferCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")  # no store_id from the body
    name: Name
    kind: Kind
    product_id: UUID | None = None
    variant_id: UUID | None = None
    percent: Percent | None = None
    fixed_price: Money | None = None
    min_order_total: Money | None = None
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None
    is_active: bool = True


class OfferUpdate(BaseModel):
    """Kind and target can't change: delete the offer and make a new one instead."""
    model_config = ConfigDict(extra="forbid")
    name: Name | None = None
    percent: Percent | None = None
    fixed_price: Money | None = None
    min_order_total: Money | None = None
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None
    is_active: bool | None = None


class OfferOut(BaseModel):
    id: UUID
    name: str
    kind: Kind
    product_id: UUID | None
    variant_id: UUID | None
    product_name: str | None
    variant_name: str | None
    percent: Decimal | None
    fixed_price: Decimal | None
    min_order_total: Decimal | None
    starts_at: datetime | None
    ends_at: datetime | None
    is_active: bool
    status: Literal["live", "scheduled", "ended", "paused"]


def _out(row: dict) -> OfferOut:
    pricing_fields = {k: row[k] for k in Offer.__dataclass_fields__}
    return OfferOut(**row, status=offer_status(Offer(**pricing_fields), datetime.now(timezone.utc)))


def _unprocessable(message: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=message)


@router.get("", response_model=list[OfferOut])
async def list_offers(
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> list[OfferOut]:
    return [_out(r) for r in await db.list_offers(conn, store_id)]


@router.post("", response_model=OfferOut, status_code=status.HTTP_201_CREATED)
async def create_offer(
    body: OfferCreate,
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> OfferOut:
    data = body.model_dump()
    try:
        check_offer(data)
    except ValueError as exc:
        raise _unprocessable(str(exc))
    if not await db.target_in_store(conn, store_id, data["product_id"], data["variant_id"]):
        raise _unprocessable("المنتج أو النسخة غير موجودة")
    try:
        offer_id = await db.insert_offer(conn, store_id, _clean_for_kind(data))
    except asyncpg.CheckViolationError:
        raise _unprocessable("بيانات العرض غير صالحة")
    return _out(await db.get_offer(conn, store_id, offer_id))


@router.patch("/{offer_id}", response_model=OfferOut)
async def update_offer(
    offer_id: UUID,
    body: OfferUpdate,
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> OfferOut:
    current = await db.get_offer(conn, store_id, offer_id)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="العرض غير موجود")
    changes = body.model_dump(exclude_unset=True)
    for required in ("name", "is_active"):
        if required in changes and changes[required] is None:
            raise _unprocessable(f"{required} لا يمكن أن يكون فارغاً")
    try:
        check_offer({**current, **changes})
    except ValueError as exc:
        raise _unprocessable(str(exc))
    try:
        await db.update_offer(conn, store_id, offer_id, changes)
    except asyncpg.CheckViolationError:
        raise _unprocessable("بيانات العرض غير صالحة")
    return _out(await db.get_offer(conn, store_id, offer_id))


@router.delete("/{offer_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_offer(
    offer_id: UUID,
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> None:
    if not await db.delete_offer(conn, store_id, offer_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="العرض غير موجود")
