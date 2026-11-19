import pytest

from app.utils.arabic import normalize_arabic, to_latin_digits


@pytest.mark.parametrize("a, b", [
    ("أحمر", "احمر"),
    ("إيشارب", "ايشارب"),
    ("آخر", "اخر"),
    ("شنطة", "شنطه"),
    ("مستشفى", "مستشفي"),
    ("فُسْتَانٌ", "فستان"),          # diacritics removed
    ("فســـتان", "فستان"),           # tatweel removed
    ("  فستان   صيفي ", "فستان صيفي"),  # whitespace collapsed
    ("مسؤول", "مسوول"),
    ("Dress", "dress"),
])
def test_equivalent_spellings_normalize_the_same(a, b):
    assert normalize_arabic(a) == normalize_arabic(b)


def test_digits_and_punctuation():
    assert normalize_arabic("مقاس ٤٢") == "مقاس 42"
    assert normalize_arabic("۵۰ml") == "50ml"
    assert normalize_arabic("شو السعر؟") == "شو السعر?"


def test_empty_values():
    assert normalize_arabic(None) == ""
    assert normalize_arabic("") == ""
    assert normalize_arabic("   ") == ""


def test_typos_are_not_corrected():
    # Spelling fixes are the matcher's job (pg_trgm), not normalization's.
    assert normalize_arabic("فسطان") != normalize_arabic("فستان")


def test_to_latin_digits_only_touches_digits():
    assert to_latin_digits("٠٥٩٩١٢٣٤٥٦") == "0599123456"
    assert to_latin_digits("أحمر 3") == "أحمر 3"
