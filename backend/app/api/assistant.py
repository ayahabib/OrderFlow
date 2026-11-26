"""Dashboard assistant (TASKS.md 3.13): POST /api/assistant -> a read-only answer.

The LLM picks one read-only tool; the answer is built from SQL by code. Nothing here writes.
store_id comes from the token only (hard rule 3).
"""

from typing import Annotated
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, StringConstraints

from app.api.auth import get_current_store_id
from app.db.pool import get_db
from app.llm.assistant_tools import pick_tool
from app.llm.client import LLM, LLMError, get_llm
from app.services import assistant

router = APIRouter(prefix="/api/assistant", tags=["assistant"])

LLM_DOWN = "ما قدرت أفهم السؤال هلق (مشكلة بخدمة الذكاء الاصطناعي). جرّب كمان شوي."


class AskIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class AskOut(BaseModel):
    reply: str


@router.post("", response_model=AskOut)
async def ask(
    body: AskIn,
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
    llm: LLM | None = Depends(get_llm),
) -> AskOut:
    if llm is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, LLM_DOWN)
    try:
        call = await pick_tool(llm, body.text)
    except LLMError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, LLM_DOWN) from None
    return AskOut(reply=await assistant.answer(conn, store_id, call))
