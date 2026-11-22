"""Knowledge base management for the owner (task 2.1, ARCHITECTURE.md section 9).

store_id always comes from the logged-in owner. Search here is the owner's "try it" box;
the customer-facing answering (thresholds, NOT_FOUND) is task 2.2.
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, ConfigDict, StringConstraints

from app.api.auth import get_current_store_id
from app.db import knowledge as db
from app.db.pool import get_db
from app.llm.client import LLMError
from app.llm.embeddings import Embedder, get_embedder
from app.services import knowledge as service

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

MAX_UPLOAD_BYTES = 200 * 1024  # plenty for a few hundred paragraphs
NOT_CONFIGURED = "البحث مش مفعّل على السيرفر (OPENAI_API_KEY فاضي)."
EMBED_FAILED = "ما قدرنا نجهّز المعلومة للبحث هلق، وما انحفظ شي. جرّب كمان شوي."
NOT_FOUND = "المعلومة مش موجودة"

Answer = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=service.MAX_ANSWER)]
Question = Annotated[str, StringConstraints(strip_whitespace=True, max_length=service.MAX_QUESTION)]


class EntryOut(BaseModel):
    id: UUID
    question: str | None
    answer: str
    source: Literal["upload", "owner_answer", "manual"]
    is_active: bool
    created_at: datetime


class EntryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: Question | None = None
    answer: Answer


class EntryPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: Question | None = None
    answer: Answer | None = None
    is_active: bool | None = None


class SearchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class SearchHit(BaseModel):
    id: UUID
    question: str | None
    answer: str
    similarity: float


def _embedder(embedder: Embedder | None = Depends(get_embedder)) -> Embedder:
    if embedder is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=NOT_CONFIGURED)
    return embedder


def _out(row) -> EntryOut:
    return EntryOut(**dict(row))


@router.get("", response_model=list[EntryOut])
async def list_entries(
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> list[EntryOut]:
    return [_out(r) for r in await db.list_entries(conn, store_id)]


@router.post("", response_model=EntryOut, status_code=status.HTTP_201_CREATED)
async def add_entry(
    body: EntryIn,
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
    embedder: Embedder = Depends(_embedder),
) -> EntryOut:
    try:
        [row] = await service.add(conn, embedder, store_id, "manual", [(body.question or None, body.answer)])
    except LLMError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=EMBED_FAILED) from None
    return _out(row)


@router.post("/upload", response_model=list[EntryOut], status_code=status.HTTP_201_CREATED)
async def upload_file(
    file: UploadFile = File(...),
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
    embedder: Embedder = Depends(_embedder),
) -> list[EntryOut]:
    if not (file.filename or "").lower().endswith((".txt", ".md")):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="ارفع ملف نص (.txt)")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="الملف أكبر من 200 كيلوبايت")
    try:
        paragraphs = service.split_paragraphs(service.decode_text_file(content))
    except service.KnowledgeFileError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from None
    try:
        rows = await service.add(conn, embedder, store_id, "upload", [(None, p) for p in paragraphs])
    except LLMError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=EMBED_FAILED) from None
    return [_out(r) for r in rows]


@router.patch("/{entry_id}", response_model=EntryOut)
async def edit_entry(
    entry_id: UUID,
    body: EntryPatch,
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
    embedder: Embedder = Depends(_embedder),
) -> EntryOut:
    current = await db.get_entry(conn, store_id, entry_id)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    changes = body.model_dump(exclude_unset=True)
    if "answer" in changes and changes["answer"] is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="الجواب ما بيصير فاضي")
    question = (changes["question"] or None) if "question" in changes else current["question"]  # "" clears it
    answer = changes.get("answer") or current["answer"]
    is_active = changes["is_active"] if changes.get("is_active") is not None else current["is_active"]
    try:
        row = await service.edit(conn, embedder, store_id, entry_id, question=question, answer=answer, is_active=is_active)
    except LLMError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=EMBED_FAILED) from None
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return _out(row)


@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_entry(
    entry_id: UUID,
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> None:
    if not await db.delete_entry(conn, store_id, entry_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)


@router.post("/search", response_model=list[SearchHit])
async def search(
    body: SearchIn,
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
    embedder: Embedder = Depends(_embedder),
) -> list[SearchHit]:
    try:
        rows = await service.search(conn, embedder, store_id, body.query)
    except LLMError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="ما قدرنا نبحث هلق. جرّب كمان شوي.") from None
    return [SearchHit(**dict(r)) for r in rows]
