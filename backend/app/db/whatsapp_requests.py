"""Requests to connect a store's WhatsApp number (task 5.4).

The owner's functions filter by store_id. The admin's (list_all, get_by_id, mark_*) read across
stores and are called only from /api/admin/* behind require_admin.
"""

from uuid import UUID

import asyncpg

COLUMNS = "id, store_id, phone, display_name, status::text as status, phone_number_id, reject_reason, created_at, handled_at"


async def latest_for_store(conn: asyncpg.Connection, store_id: UUID) -> asyncpg.Record | None:
    return await conn.fetchrow(
        f"select {COLUMNS} from whatsapp_requests where store_id = $1 order by created_at desc limit 1", store_id,
    )


async def create(conn: asyncpg.Connection, store_id: UUID, phone: str, display_name: str) -> asyncpg.Record | None:
    """None when the store already has an open request (one at a time)."""
    return await conn.fetchrow(
        f"""
        insert into whatsapp_requests (store_id, phone, display_name) values ($1, $2, $3)
        on conflict (store_id) where status = 'pending' do nothing
        returning {COLUMNS}
        """,
        store_id, phone, display_name,
    )


async def cancel_open(conn: asyncpg.Connection, store_id: UUID) -> bool:
    """The owner withdraws an open request. True if there was one."""
    deleted = await conn.fetchval(
        "delete from whatsapp_requests where store_id = $1 and status = 'pending' returning id", store_id,
    )
    return deleted is not None


async def list_all(conn: asyncpg.Connection, status: str | None) -> list[asyncpg.Record]:
    """Admin: every store's requests, open ones oldest first (the queue), then the rest newest first."""
    return await conn.fetch(
        f"""
        select r.id, r.store_id, s.name as store_name, r.phone, r.display_name, r.status::text as status,
               r.phone_number_id, r.reject_reason, r.created_at, r.handled_at
          from whatsapp_requests r join stores s on s.id = r.store_id
         where ($1::text is null or r.status::text = $1)
         order by (r.status = 'pending') desc,
                  case when r.status = 'pending' then r.created_at end asc,
                  r.created_at desc
        """,
        status,
    )


async def get_by_id(conn: asyncpg.Connection, request_id: UUID) -> asyncpg.Record | None:
    return await conn.fetchrow(f"select {COLUMNS} from whatsapp_requests where id = $1", request_id)


async def mark_connected(conn: asyncpg.Connection, request_id: UUID, phone_number_id: str) -> None:
    await conn.execute(
        "update whatsapp_requests set status = 'connected', phone_number_id = $2, handled_at = now() where id = $1",
        request_id, phone_number_id,
    )


async def mark_rejected(conn: asyncpg.Connection, request_id: UUID, reason: str) -> None:
    await conn.execute(
        "update whatsapp_requests set status = 'rejected', reject_reason = $2, handled_at = now() where id = $1",
        request_id, reason,
    )
