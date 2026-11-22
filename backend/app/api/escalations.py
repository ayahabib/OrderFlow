"""Escalated questions and the owner's answer (task 2.3, ARCHITECTURE.md section 7).

The answer goes to the customer on the conversation's own channel (sender 'owner'), the
escalation becomes 'answered' with the owner's choice:
- save_to_kb = true  -> "save for the bot": the Q&A (optionally reworded) is embedded and added
  to the knowledge base (source owner_answer), so similar questions get answered next time (2.4).
  Only for questions the bot is allowed to answer: forced rules (complaint, discount, allergy,
  "talk to a person"...) always go to the owner, so saving them would do nothing or break rule 5.
- save_to_kb = false -> "private answer": never stored in the knowledge base (hard rule 8).
`then` picks who continues: the bot again, or the owner. If other questions in the same
conversation are still open, it stays with the owner until those are answered too.
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, StringConstraints

from app.api.auth import get_current_store_id
from app.api.conversations import CHANNEL_UNAVAILABLE, ConversationDetail, _detail, _locked
from app.config import Settings, get_settings
from app.db import knowledge as kb_db
from app.db.pool import get_db
from app.llm.client import LLMError
from app.llm.embeddings import Embedder, get_embedder
from app.services import owner_messages
from app.services.knowledge import MAX_ANSWER, MAX_QUESTION, embedding_text

router = APIRouter(prefix="/api/escalations", tags=["escalations"])

EscalationStatus = Literal["open", "answered", "dismissed"]
# Rules whose questions the bot may answer from the knowledge base later.
LEARNABLE_RULES = {"not_in_data", "llm_failure"}
EMBED_FAILED = "ما قدرنا نجهّز الجواب للبوت هلق، وما انبعت شي. جرّب كمان مرة، أو أرسله كجواب خاص."


class EscalationListItem(BaseModel):
    id: UUID
    conversation_id: UUID
    rule: str | None
    question: str
    status: EscalationStatus
    customer_name: str | None
    channel: str
    created_at: datetime


class AnswerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    save_to_kb: bool                      # explicit choice every time, no default (hard rule 8)
    then: Literal["bot", "owner"] = "bot"
    # Optional rewording for the knowledge base (defaults: the customer's question, the answer above).
    kb_question: Annotated[str, StringConstraints(strip_whitespace=True, max_length=MAX_QUESTION)] | None = None
    kb_answer: Annotated[str, StringConstraints(strip_whitespace=True, max_length=MAX_ANSWER)] | None = None


@router.get("", response_model=list[EscalationListItem])
async def list_escalations(
    status_filter: Annotated[EscalationStatus | None, Query(alias="status")] = "open",
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> list[EscalationListItem]:
    rows = await conn.fetch(
        """
        select e.id, e.conversation_id, e.rule, e.question, e.status::text as status, e.created_at,
               cu.name as customer_name, c.channel::text as channel
          from escalations e
          join conversations c on c.id = e.conversation_id and c.store_id = e.store_id
          join customers cu on cu.id = c.customer_id and cu.store_id = c.store_id
         where e.store_id = $1 and ($2::text is null or e.status::text = $2)
         order by e.created_at desc
         limit 200
        """,
        store_id, status_filter,
    )
    return [EscalationListItem(**dict(r)) for r in rows]


@router.post("/{escalation_id}/answer", response_model=ConversationDetail)
async def answer_escalation(
    escalation_id: UUID,
    body: AnswerIn,
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
    embedder: Embedder | None = Depends(get_embedder),
) -> ConversationDetail:
    current = await conn.fetchrow(
        "select rule, question from escalations where id = $1 and store_id = $2", escalation_id, store_id)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="السؤال مش موجود")
    if not body.save_to_kb and (body.kb_question or body.kb_answer):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="الصياغة للبوت بس مع \"احفظه للبوت\".")

    learned: tuple[str, str, list[float]] | None = None
    if body.save_to_kb:
        if current["rule"] not in LEARNABLE_RULES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail="هالنوع من الأسئلة دايماً بيتحوّل إلك، فما في فايدة تحفظه للبوت. أرسله كجواب خاص.")
        if embedder is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="الحفظ للبوت مش مفعّل على السيرفر (OPENAI_API_KEY).")
        question = body.kb_question or current["question"][:MAX_QUESTION]
        answer = body.kb_answer or body.answer
        # Embed before anything is sent or saved: if it fails, nothing happened and the owner retries.
        try:
            [vector] = await embedder.embed([embedding_text(question, answer)])
        except LLMError:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=EMBED_FAILED) from None
        learned = (question, answer, vector)

    async with conn.transaction():
        conversation_id = await conn.fetchval(
            "select conversation_id from escalations where id = $1 and store_id = $2", escalation_id, store_id)
        if conversation_id is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="السؤال مش موجود")
        conversation = await _locked(conn, store_id, conversation_id)   # same lock order as the engine
        escalation_status = await conn.fetchval(
            "select status::text from escalations where id = $1 and store_id = $2 for update", escalation_id, store_id)
        if escalation_status != "open":
            raise HTTPException(status.HTTP_409_CONFLICT, detail="هالسؤال انجاوب قبل.")
        try:
            adapter = await owner_messages.store(conn, settings, store_id, conversation, body.answer)
        except owner_messages.ChannelUnavailable:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=CHANNEL_UNAVAILABLE) from None

        await conn.execute(
            """
            update escalations
               set status = 'answered', owner_answer = $3, save_to_kb = $4, answered_at = now()
             where id = $1 and store_id = $2
            """,
            escalation_id, store_id, body.answer, body.save_to_kb,
        )
        if learned is not None:  # same transaction: the answer and what the bot learns go together
            await kb_db.insert_entries(conn, store_id, "owner_answer", [learned], escalation_id=escalation_id)
        still_open = await conn.fetchval(
            "select count(*) from escalations where conversation_id = $1 and store_id = $2 and status = 'open'",
            conversation_id, store_id,
        )
        if body.then == "bot" and still_open == 0:
            await conn.execute(
                "update conversations set status = 'bot', bot_paused = false where id = $1 and store_id = $2",
                conversation_id, store_id)
        else:
            await conn.execute("update conversations set status = 'owner' where id = $1 and store_id = $2",
                               conversation_id, store_id)
    await owner_messages.deliver(adapter, conversation, body.answer)
    return await _detail(conn, settings, store_id, conversation_id)
