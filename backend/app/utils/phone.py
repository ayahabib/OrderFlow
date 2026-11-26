"""Phone numbers to one E.164 form ("+970599123456") so the same customer is recognised
across channels and however they typed the number (task 3.9).

The store's country code fills in local numbers ("0599 123 456" -> "+970599123456").
supabase/migrations/0021_backfill_customer_phone.sql backfills old rows with the same rules.
"""

import re

from app.utils.arabic import to_latin_digits

_SEPARATORS = re.compile(r"[\s\-().]")
_E164 = re.compile(r"^\+[1-9][0-9]{7,14}$")
COUNTRY_CODE = re.compile(r"^[1-9][0-9]{0,2}$")


def normalize_phone(raw: str | None, country_code: str) -> str | None:
    """E.164 string, or None when it doesn't look like a phone number."""
    if not raw:
        return None
    d = _SEPARATORS.sub("", to_latin_digits(raw.strip()))
    if d.startswith("+"):
        digits = d[1:]
    elif d.startswith("00"):
        digits = d[2:]
    elif d.startswith("0"):
        digits = country_code + d[1:]
    elif d.startswith(country_code) and len(d) > len(country_code) + 7:
        digits = d  # "970599123456" typed without the +
    else:
        digits = country_code + d  # "599123456"
    result = "+" + digits
    return result if _E164.fullmatch(result) else None
