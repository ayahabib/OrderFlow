"""Find the delivery zone for an address, from the store's own zones only (hard rule 4: never invent a fee)."""

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

import asyncpg

from app.utils.arabic import normalize_arabic


@dataclass(frozen=True)
class Zone:
    id: UUID
    name: str
    fee: Decimal


async def active_zones(conn: asyncpg.Connection, store_id: UUID) -> list[Zone]:
    rows = await conn.fetch(
        "select id, name, fee from delivery_zones where store_id = $1 and is_active order by name", store_id
    )
    return [Zone(**dict(r)) for r in rows]


def _strip_article(word: str) -> str:
    return word[2:] if word.startswith("ال") and len(word) > 3 else word


def find_zone(text: str, zones: list[Zone]) -> Zone | None:
    """The zone whose name appears in the text ("نابلس، شارع فيصل" -> نابلس). Longest name wins."""
    norm = normalize_arabic(text)
    words = {_strip_article(w) for w in norm.replace(",", " ").replace("،", " ").split()}
    hits = []
    for zone in zones:
        name = normalize_arabic(zone.name)
        name_words = [_strip_article(w) for w in name.split()]
        if f" {name} " in f" {norm} " or all(w in words for w in name_words):
            hits.append(zone)
    return max(hits, key=lambda z: len(z.name), default=None)
