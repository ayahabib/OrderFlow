"""Platform admin API (tasks 5.2-5.4). Every route needs `require_admin` (router dependency).

The only routes allowed to read across stores (CLAUDE.md hard rule 3). They never return
customer messages or customer details, and every change is written to audit_logs with
actor 'admin' under the store it touched.
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, StringConstraints

from app.api.auth import CurrentUser, require_admin
from app.channels.whatsapp import platform_token
from app.channels.whatsapp_api import WhatsAppAPI, WhatsAppError
from app.config import Settings, get_settings
from app.db import admin as db
from app.db import channels as channels_db
from app.db import whatsapp_requests as requests_db
from app.db.pool import get_db
from app.services import audit
from app.utils.crypto import CryptoError, encrypt_json

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])


async def _log(conn, store_id: UUID, action: str, user: CurrentUser, before=None, after=None, entity_id=None):
    # The admin's user id goes in the row so changes can be traced to a person.
    await audit.log(conn, store_id, "admin", action, entity_id or store_id,
                    before=before, after={**(after or {}), "admin_user_id": str(user.user_id)})


# --- stores (5.2) -----------------------------------------------------------------------------

class AdminStore(BaseModel):
    id: UUID
    name: str
    owner_email: str | None
    created_at: datetime
    currency: str
    is_demo: bool
    suspended_at: datetime | None
    bot_enabled: bool
    channels: list[str]
    last_activity: datetime | None
    orders_count: int


@router.get("/stores", response_model=list[AdminStore])
async def list_stores(conn: asyncpg.Connection = Depends(get_db)) -> list[AdminStore]:
    return [AdminStore(**dict(r)) for r in await db.list_stores(conn)]


async def _store_or_404(conn, store_id: UUID) -> asyncpg.Record:
    row = await db.get_store_flags(conn, store_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="المتجر مش موجود")
    return row


@router.post("/stores/{store_id}/suspend", status_code=status.HTTP_204_NO_CONTENT)
async def suspend_store(store_id: UUID, user: CurrentUser = Depends(require_admin),
                        conn: asyncpg.Connection = Depends(get_db)) -> None:
    """The store's bot stops answering; its messages are still stored and the owner sees a banner."""
    row = await _store_or_404(conn, store_id)
    await db.set_suspended(conn, store_id, True)
    await _log(conn, store_id, "admin.suspend_store", user,
               before={"suspended": row["suspended_at"] is not None}, after={"suspended": True})


@router.post("/stores/{store_id}/unsuspend", status_code=status.HTTP_204_NO_CONTENT)
async def unsuspend_store(store_id: UUID, user: CurrentUser = Depends(require_admin),
                          conn: asyncpg.Connection = Depends(get_db)) -> None:
    row = await _store_or_404(conn, store_id)
    await db.set_suspended(conn, store_id, False)
    await _log(conn, store_id, "admin.unsuspend_store", user,
               before={"suspended": row["suspended_at"] is not None}, after={"suspended": False})


class StoreFlagsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    is_demo: bool


@router.patch("/stores/{store_id}", status_code=status.HTTP_204_NO_CONTENT)
async def update_store_flags(store_id: UUID, body: StoreFlagsIn, user: CurrentUser = Depends(require_admin),
                             conn: asyncpg.Connection = Depends(get_db)) -> None:
    row = await _store_or_404(conn, store_id)
    await db.set_demo(conn, store_id, body.is_demo)
    await _log(conn, store_id, "admin.set_demo", user, before={"is_demo": row["is_demo"]}, after={"is_demo": body.is_demo})


# --- platform numbers (5.3) -------------------------------------------------------------------

class CurrencySales(BaseModel):
    currency: str
    orders: int
    orders_by_bot: int
    revenue: Decimal


class ChannelCount(BaseModel):
    channel: str
    conversations: int
    orders: int


class TopStore(BaseModel):
    id: UUID
    name: str
    orders: int


class GrowthPoint(BaseModel):
    start: datetime
    orders: int
    new_stores: int
    revenue: dict[str, Decimal]   # per currency, never added across currencies


class Overview(BaseModel):
    include_demo: bool
    stores_total: int
    stores_active_30d: int
    stores_new_this_month: int
    orders_total: int
    orders_by_bot: int
    sales: list[CurrencySales]
    conversations_total: int
    conversations_handed_over: int
    bot_share: float | None        # conversations the bot handled alone, 0..1 (None when there are none)
    channels: list[ChannelCount]
    top_stores: list[TopStore]
    period: str
    growth: list[GrowthPoint]


@router.get("/overview", response_model=Overview)
async def overview(
    include_demo: bool = False,
    period: Annotated[Literal["week", "month"], Query()] = "week",
    conn: asyncpg.Connection = Depends(get_db),
) -> Overview:
    """Platform totals for promotion. Every number comes from SQL (hard rule 1)."""
    stores = await db.store_counts(conn, include_demo)
    sales = [CurrencySales(**dict(r)) for r in await db.sales_by_currency(conn, include_demo)]
    convs = await db.conversation_counts(conn, include_demo)
    total, handed = convs["total"], convs["handed_over"]
    return Overview(
        include_demo=include_demo,
        stores_total=stores["total"], stores_active_30d=stores["active_30d"],
        stores_new_this_month=stores["new_this_month"],
        orders_total=sum(s.orders for s in sales), orders_by_bot=sum(s.orders_by_bot for s in sales),
        sales=sales,
        conversations_total=total, conversations_handed_over=handed,
        bot_share=round((total - handed) / total, 4) if total else None,
        channels=[ChannelCount(**dict(r)) for r in await db.by_channel(conn, include_demo)],
        top_stores=[TopStore(**dict(r)) for r in await db.top_stores(conn, include_demo)],
        period=period,
        growth=[GrowthPoint(**p) for p in await db.growth(conn, include_demo, period)],
    )


# --- WhatsApp requests (5.4) ------------------------------------------------------------------

class AdminWhatsAppRequest(BaseModel):
    id: UUID
    store_id: UUID
    store_name: str
    phone: str
    display_name: str
    status: str
    phone_number_id: str | None
    reject_reason: str | None
    created_at: datetime
    handled_at: datetime | None


@router.get("/whatsapp-requests", response_model=list[AdminWhatsAppRequest])
async def list_whatsapp_requests(
    status_: Annotated[Literal["pending", "connected", "rejected"] | None, Query(alias="status")] = None,
    conn: asyncpg.Connection = Depends(get_db),
) -> list[AdminWhatsAppRequest]:
    return [AdminWhatsAppRequest(**dict(r)) for r in await requests_db.list_all(conn, status_)]


async def _open_request(conn, request_id: UUID) -> asyncpg.Record:
    row = await requests_db.get_by_id(conn, request_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="الطلب مش موجود")
    if row["status"] != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="الطلب انعالج من قبل")
    return row


class ConnectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phone_number_id: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\d{6,25}$")]


class ConnectOut(BaseModel):
    display_phone_number: str | None
    verified_name: str | None


@router.post("/whatsapp-requests/{request_id}/connect", response_model=ConnectOut)
async def connect_whatsapp_request(
    request_id: UUID, body: ConnectIn,
    user: CurrentUser = Depends(require_admin),
    conn: asyncpg.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ConnectOut:
    """Link a number the admin added on the platform's WABA to the requesting store.

    The connection is tested with Meta first (the platform token must reach this number), so
    nothing is saved for a wrong Phone Number ID.
    """
    req = await _open_request(conn, request_id)
    token = platform_token(settings)
    if token is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="WHATSAPP_SYSTEM_USER_TOKEN مش محطوط بإعدادات السيرفر.")
    try:
        encrypt_json(settings, {})
    except CryptoError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="ربط القنوات مش مفعّل على السيرفر (CHANNEL_ENCRYPTION_KEY).") from None
    try:
        info = await WhatsAppAPI(token, settings.whatsapp_graph_version).phone_number(body.phone_number_id)
    except WhatsAppError as exc:
        if exc.status in (400, 401, 403, 404) or exc.code in (100, 190):
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                detail="الـ Phone Number ID غلط، أو توكن المنصة ما إله صلاحية على هالرقم.") from None
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="ما قدرنا نتواصل مع واتساب (Meta) هلق.") from None

    if await channels_db.external_id_taken(conn, "whatsapp", body.phone_number_id, req["store_id"]):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="هالرقم مربوط بمتجر تاني.")
    # No token in the credentials: this number sends with the platform's System User token.
    creds = {"display_phone_number": info.get("display_phone_number"), "verified_name": info.get("verified_name"),
             "via": "platform"}
    async with conn.transaction():
        await channels_db.save_channel(conn, req["store_id"], "whatsapp", body.phone_number_id,
                                       encrypt_json(settings, creds))
        await requests_db.mark_connected(conn, request_id, body.phone_number_id)
        await _log(conn, req["store_id"], "admin.connect_whatsapp", user, entity_id=request_id,
                   after={"phone_number_id": body.phone_number_id})
    return ConnectOut(display_phone_number=info.get("display_phone_number"), verified_name=info.get("verified_name"))


class RejectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=300)]


@router.post("/whatsapp-requests/{request_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
async def reject_whatsapp_request(
    request_id: UUID, body: RejectIn,
    user: CurrentUser = Depends(require_admin),
    conn: asyncpg.Connection = Depends(get_db),
) -> None:
    req = await _open_request(conn, request_id)
    async with conn.transaction():
        await requests_db.mark_rejected(conn, request_id, body.reason)
        await _log(conn, req["store_id"], "admin.reject_whatsapp", user, entity_id=request_id)
