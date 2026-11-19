"""Arabic text normalization for search and matching.

Used before storing search_text, before matching product names, and in evals.
It is lossy on purpose: two spellings a customer might type should normalize
to the same string ("أحمر" / "احمر", "شنطة" / "شنطه").
"""

import re

# Harakat (fathatan .. sukun), superscript alef, Quranic marks.
_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭ]")
_TATWEEL = "ـ"
_SPACES = re.compile(r"\s+")

_CHAR_MAP = str.maketrans({
    # Alef forms -> bare alef
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    # Ta marbuta -> ha, alef maqsura -> ya
    "ة": "ه", "ى": "ي",
    # Hamza on carriers -> the carrier letter
    "ؤ": "و", "ئ": "ي",
    # Arabic-Indic and Persian digits -> Latin digits
    **{chr(0x0660 + i): str(i) for i in range(10)},
    **{chr(0x06F0 + i): str(i) for i in range(10)},
    # Arabic punctuation -> Latin equivalents
    "،": ",", "؛": ";", "؟": "?",
})


def normalize_arabic(text: str | None) -> str:
    """Normalize Arabic (and mixed) text for comparison. Returns "" for None."""
    if not text:
        return ""
    text = _DIACRITICS.sub("", text).replace(_TATWEEL, "")
    text = text.translate(_CHAR_MAP).lower()
    return _SPACES.sub(" ", text).strip()


def to_latin_digits(text: str) -> str:
    """Only convert Arabic-Indic digits (for numbers such as quantities and phones)."""
    return text.translate({**{0x0660 + i: str(i) for i in range(10)}, **{0x06F0 + i: str(i) for i in range(10)}})
