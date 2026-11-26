"""Delivery zone queries. Every query filters by store_id."""

from uuid import UUID

import asyncpg

COLUMNS = "id, name, fee, is_active, is_pickup"


async def list_zones(conn: asyncpg.Connection, store_id: UUID) -> list[dict]:
    rows = await conn.fetch(f"select {COLUMNS} from delivery_zones where store_id = $1 order by name", store_id)
    return [dict(r) for r in rows]


async def get_zone(conn: asyncpg.Connection, store_id: UUID, zone_id: UUID) -> dict | None:
    row = await conn.fetchrow(f"select {COLUMNS} from delivery_zones where id = $1 and store_id = $2", zone_id, store_id)
    return dict(row) if row else None


async def insert_zone(conn: asyncpg.Connection, store_id: UUID, name: str, fee, is_active: bool,
                      is_pickup: bool = False) -> dict:
    row = await conn.fetchrow(
        f"""insert into delivery_zones (store_id, name, fee, is_active, is_pickup)
            values ($1, $2, $3, $4, $5) returning {COLUMNS}""",
        store_id, name, fee, is_active, is_pickup,
    )
    return dict(row)


EDITABLE = ("name", "fee", "is_active", "is_pickup")


async def update_zone(conn: asyncpg.Connection, store_id: UUID, zone_id: UUID, changes: dict) -> dict | None:
    fields = {k: v for k, v in changes.items() if k in EDITABLE}
    if not fields:
        return await get_zone(conn, store_id, zone_id)
    # Column names come from the fixed EDITABLE whitelist; values are parameters.
    sets = ", ".join(f"{col} = ${i}" for i, col in enumerate(fields, start=3))
    row = await conn.fetchrow(
        f"update delivery_zones set {sets} where id = $1 and store_id = $2 returning {COLUMNS}",
        zone_id, store_id, *fields.values(),
    )
    return dict(row) if row else None


async def delete_zone(conn: asyncpg.Connection, store_id: UUID, zone_id: UUID) -> bool:
    """Raises ForeignKeyViolationError if orders use the zone (own transaction, so the caller's stays usable)."""
    async with conn.transaction():
        status = await conn.execute("delete from delivery_zones where id = $1 and store_id = $2", zone_id, store_id)
    return status != "DELETE 0"
