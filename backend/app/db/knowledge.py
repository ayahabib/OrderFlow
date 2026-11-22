"""knowledge_base queries. Every query filters by store_id (hard rule 3).

Vectors go to Postgres as text ('[0.1,0.2,...]') cast to vector: no extra asyncpg codec.
"""

from uuid import UUID

import asyncpg

COLUMNS = "id, question, answer, source::text as source, is_active, created_at"


def vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in vector) + "]"


async def list_entries(conn: asyncpg.Connection, store_id: UUID) -> list[asyncpg.Record]:
    return await conn.fetch(
        f"select {COLUMNS} from knowledge_base where store_id = $1 order by created_at desc, id", store_id)


async def get_entry(conn: asyncpg.Connection, store_id: UUID, entry_id: UUID) -> asyncpg.Record | None:
    return await conn.fetchrow(
        f"select {COLUMNS} from knowledge_base where id = $1 and store_id = $2", entry_id, store_id)


async def insert_entries(conn: asyncpg.Connection, store_id: UUID, source: str,
                         rows: list[tuple[str | None, str, list[float]]],
                         escalation_id: UUID | None = None) -> list[asyncpg.Record]:
    """rows: (question, answer, embedding). One statement per row, inside the caller's transaction.
    escalation_id links an owner answer to the question it came from (task 2.4)."""
    out = []
    for question, answer, embedding in rows:
        out.append(await conn.fetchrow(
            f"""
            insert into knowledge_base (store_id, question, answer, source, embedding, escalation_id)
            values ($1, $2, $3, $4::kb_source, $5::vector, $6)
            returning {COLUMNS}
            """,
            store_id, question, answer, source, vector_literal(embedding), escalation_id,
        ))
    return out


async def update_entry(conn: asyncpg.Connection, store_id: UUID, entry_id: UUID, *, question: str | None,
                       answer: str, is_active: bool, embedding: list[float] | None) -> asyncpg.Record | None:
    """embedding=None keeps the stored one (the text didn't change)."""
    return await conn.fetchrow(
        f"""
        update knowledge_base
           set question = $3, answer = $4, is_active = $5,
               embedding = coalesce($6::vector, embedding)
         where id = $1 and store_id = $2
        returning {COLUMNS}
        """,
        entry_id, store_id, question, answer, is_active, vector_literal(embedding) if embedding else None,
    )


async def delete_entry(conn: asyncpg.Connection, store_id: UUID, entry_id: UUID) -> bool:
    status = await conn.execute("delete from knowledge_base where id = $1 and store_id = $2", entry_id, store_id)
    return status != "DELETE 0"


async def match(conn: asyncpg.Connection, store_id: UUID, embedding: list[float], count: int) -> list[asyncpg.Record]:
    """Closest active entries of this store (match_knowledge filters by store_id and is_active)."""
    return await conn.fetch(
        """
        select m.id, m.question, m.answer, m.similarity
          from match_knowledge($1, $2::vector, $3) m
         where m.similarity is not null
        """,
        store_id, vector_literal(embedding), count,
    )
