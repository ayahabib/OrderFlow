"""Store knowledge base (task 2.1): owner-written info the bot will answer from in 2.2.

- Each entry: an optional question + an answer. The embedding is made from both, so a
  customer's question can match either the owner's wording of the question or the answer.
- Upload: a plain-text file; every paragraph (separated by a blank line) becomes one entry.
- Embedding first, DB after: if OpenAI fails, nothing is saved (the owner just retries).
"""

import re
from uuid import UUID

import asyncpg

from app.db import knowledge as db
from app.llm.embeddings import Embedder

MAX_ANSWER = 2000
MAX_QUESTION = 300
MAX_UPLOAD_ENTRIES = 200
_BLANK_LINES = re.compile(r"\n\s*\n")


class KnowledgeFileError(ValueError):
    """The uploaded file can't be used; the message is shown to the owner."""


def embedding_text(question: str | None, answer: str) -> str:
    return f"{question}\n{answer}" if question else answer


def split_paragraphs(text: str) -> list[str]:
    """Paragraphs of a plain-text file, whitespace tidied. Raises KnowledgeFileError for bad files."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = [" ".join(p.split()) for p in _BLANK_LINES.split(text)]
    paragraphs = [p for p in paragraphs if p]
    if not paragraphs:
        raise KnowledgeFileError("الملف فاضي")
    if len(paragraphs) > MAX_UPLOAD_ENTRIES:
        raise KnowledgeFileError(f"الملف فيه أكتر من {MAX_UPLOAD_ENTRIES} فقرة")
    too_long = [i + 1 for i, p in enumerate(paragraphs) if len(p) > MAX_ANSWER]
    if too_long:
        raise KnowledgeFileError(f"فقرات أطول من {MAX_ANSWER} حرف: {', '.join(map(str, too_long[:5]))}. قسّمها بسطر فاضي.")
    return paragraphs


def decode_text_file(content: bytes) -> str:
    if b"\x00" in content:  # binary (Word, PDF, image...): cp1256 below would "decode" anything
        raise KnowledgeFileError("هاد مش ملف نص. احفظ المعلومات كملف .txt")
    for encoding in ("utf-8-sig", "cp1256"):  # UTF-8, or Windows Arabic from older editors
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise KnowledgeFileError("ما قدرنا نقرأ الملف. احفظه كـ UTF-8 نص عادي (.txt).")


async def add(conn: asyncpg.Connection, embedder: Embedder, store_id: UUID, source: str,
              items: list[tuple[str | None, str]]) -> list[asyncpg.Record]:
    """Embed then insert all items in one transaction. Raises LLMError (nothing saved) if embedding fails."""
    vectors = await embedder.embed([embedding_text(q, a) for q, a in items])
    async with conn.transaction():
        return await db.insert_entries(conn, store_id, source, [(q, a, v) for (q, a), v in zip(items, vectors)])


async def edit(conn: asyncpg.Connection, embedder: Embedder, store_id: UUID, entry_id: UUID, *,
               question: str | None, answer: str, is_active: bool) -> asyncpg.Record | None:
    current = await db.get_entry(conn, store_id, entry_id)
    if current is None:
        return None
    text_changed = (question, answer) != (current["question"], current["answer"])
    vector = (await embedder.embed([embedding_text(question, answer)]))[0] if text_changed else None
    return await db.update_entry(conn, store_id, entry_id, question=question, answer=answer,
                                 is_active=is_active, embedding=vector)


async def search(conn: asyncpg.Connection, embedder: Embedder, store_id: UUID, query: str, count: int = 3) -> list[asyncpg.Record]:
    vector = (await embedder.embed([query]))[0]
    return await db.match(conn, store_id, vector, count)
