"""Find the delivery zone for an address, from the store's own zones only (hard rule 4: never invent a fee)."""

import re
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
    is_pickup: bool = False   # pickup from the store (3.15)


async def active_zones(conn: asyncpg.Connection, store_id: UUID) -> list[Zone]:
    rows = await conn.fetch(
        "select id, name, fee, is_pickup from delivery_zones where store_id = $1 and is_active order by name", store_id
    )
    return [Zone(**dict(r)) for r in rows]


# "I'll pick it up" in the customer's words (compared after normalize_arabic).
_PICKUP_WORDS = ("استلام", "استلم", "بستلم", "منستلم", "بمر", "بمرق", "باخده", "باخذه", "بوخده", "من المحل")


def wants_pickup(text: str) -> bool:
    norm = " ".join(re.sub(r"[^\w\s]", " ", normalize_arabic(text)).split())
    words = {_strip_article(w) for w in norm.split()}
    return any((normalize_arabic(w) in words) if " " not in w else (f" {normalize_arabic(w)} " in f" {norm} ")
               for w in _PICKUP_WORDS)


def pickup_zone(zones: list["Zone"]) -> "Zone | None":
    return next((z for z in zones if z.is_pickup), None)


def _strip_article(word: str) -> str:
    return word[2:] if word.startswith("ال") and len(word) > 3 else word


def find_zone(text: str, zones: list[Zone]) -> Zone | None:
    """The zone whose name appears in the text ("نابلس، شارع فيصل" -> نابلس). Longest name wins."""
    # Punctuation off ("نابلس؟" / "نابلس." -> نابلس); normalize_arabic turns ، ؟ into , ?
    norm = " ".join(re.sub(r"[^\w\s]", " ", normalize_arabic(text)).split())
    words = {_strip_article(w) for w in norm.split()}
    # Attached prepositions: "لرام الله" -> رام, "للخليل" -> خليل, "بنابلس" -> نابلس
    words |= {_strip_article(w[2:] if w.startswith("لل") else w[1:]) for w in norm.split()
              if len(w) > 3 and w[0] in "لبو"}
    hits = []
    for zone in zones:
        name = normalize_arabic(zone.name)
        name_words = [_strip_article(w) for w in name.split()]
        if f" {name} " in f" {norm} " or all(w in words for w in name_words):
            hits.append(zone)
    return max(hits, key=lambda z: len(z.name), default=None)
