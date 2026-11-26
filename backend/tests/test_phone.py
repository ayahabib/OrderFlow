"""Phone normalization to E.164 (task 3.9). No DB."""

import pytest

from app.utils.phone import normalize_phone


@pytest.mark.parametrize("raw", [
    "0599123456",
    "0599 123 456",
    "059-912-3456",
    "(0599) 123456",
    "+970599123456",
    "+970 599 123 456",
    "00970599123456",
    "970599123456",
    "599123456",
    "٠٥٩٩١٢٣٤٥٦",          # Arabic-Indic digits
    " 0599123456 ",
])
def test_same_number_in_many_forms(raw):
    assert normalize_phone(raw, "970") == "+970599123456"


def test_country_code_comes_from_the_store():
    assert normalize_phone("0791234567", "962") == "+962791234567"


def test_other_country_with_plus_is_kept():
    assert normalize_phone("+962791234567", "970") == "+962791234567"


@pytest.mark.parametrize("raw", [None, "", "abc", "12", "+0123456789", "+1234567890123456"])
def test_not_a_phone(raw):
    assert normalize_phone(raw, "970") is None
