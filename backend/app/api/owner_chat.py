"""Owner stock chat (task 3.3, ARCHITECTURE.md sections 8-9).

POST /api/owner-chat                      -> a reply, maybe with a pending action (nothing changes)
POST /api/owner-chat/confirm/{action_id}  -> runs the pending action (the only write)
POST /api/owner-chat/cancel/{action_id}   -> drops it
store_id comes from the token only (hard rule 3).
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, StringConstraints

from app.api.auth import get_current_store_id
from app.db.pool import get_db
from app.llm.client import LLM, LLMError, get_llm
from app.llm.owner_tools import GetStockStatus, ProposeStockAdjustment, pick_tool
from app.services import owner_chat

router = APIRouter(prefix="/api/owner-chat", tags=["owner-chat"])

LLM_DOWN = "ما قدرت أفهم الرسالة هلق (مشكلة بخدمة الذكاء الاصطناعي). ما تغيّر شي، جرّب كمان شوي."

ERROR_STATUS = {"not_found": status.HTTP_404_NOT_FOUND, "expired": status.HTTP_410_GONE,
                "invalid": status.HTTP_409_CONFLICT}


class ChatIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class PendingActionOut(BaseModel):
    id: UUID
    product_name: str
    variant_name: str
    old_stock: int
    new_stock: int
    expires_at: datetime


class ChatOut(BaseModel):
    reply: str
    action: PendingActionOut | None = None


class ConfirmOut(BaseModel):
    reply: str
    product_name: str
    variant_name: str
    old_stock: int
    new_stock: int


@router.post("", response_model=ChatOut)
async def chat(
    body: ChatIn,
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
    llm: LLM | None = Depends(get_llm),
) -> ChatOut:
    if llm is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, LLM_DOWN)
    try:
        call = await pick_tool(llm, body.text)
    except LLMError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, LLM_DOWN) from None

    if isinstance(call, ProposeStockAdjustment):
        result = await owner_chat.propose_adjustment(conn, store_id, call)
    elif isinstance(call, GetStockStatus):
        result = await owner_chat.stock_status(conn, store_id, call)
    else:
        result = owner_chat.ChatResult(owner_chat.HELP)

    p = result.proposal
    action = PendingActionOut(id=p.action_id, product_name=p.product_name, variant_name=p.variant_name,
                              old_stock=p.old_stock, new_stock=p.new_stock, expires_at=p.expires_at) if p else None
    return ChatOut(reply=result.reply, action=action)


@router.post("/confirm/{action_id}", response_model=ConfirmOut)
async def confirm(
    action_id: UUID,
    request: Request,
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> ConfirmOut:
    try:
        done = await owner_chat.confirm(conn, store_id, action_id)
    except owner_chat.ActionError as exc:
        raise HTTPException(ERROR_STATUS[exc.code], str(exc)) from None
    label = owner_chat.label(done.product_name, done.variant_name)
    request.state.audit = {"before": {"stock_quantity": done.old_stock, "product": label},
                           "after": {"stock_quantity": done.new_stock, "product": label}}   # 3.14
    return ConfirmOut(reply=f"تم. {label}: الكمية صارت {done.new_stock} (كانت {done.old_stock}).",
                      product_name=done.product_name, variant_name=done.variant_name,
                      old_stock=done.old_stock, new_stock=done.new_stock)


@router.post("/cancel/{action_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel(
    action_id: UUID,
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> None:
    if not await owner_chat.cancel(conn, store_id, action_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "هالإجراء مش موجود أو انتفذ أو انلغى من قبل.")
