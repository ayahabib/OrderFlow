"""Excel/CSV import parsing and grouping (no database)."""

import io
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import Workbook

from app.services.excel_import import (
    DEFAULT_VARIANT, ImportFileError, plan_from_file, variant_name,
)

SAMPLE = Path(__file__).resolve().parents[2] / "eval" / "sample_inventory.csv"


def csv_bytes(*lines: str) -> bytes:
    return ("\n".join(lines) + "\n").encode("utf-8")


def by_name(plan):
    return {p.name: p for p in plan.products}


# --- the sample file from eval/ ---------------------------------------------

def test_sample_inventory_groups_into_products_and_variants():
    plan = plan_from_file("sample_inventory.csv", SAMPLE.read_bytes())
    assert plan.errors == []
    assert len(plan.products) == 7
    assert plan.variant_count == 21

    products = by_name(plan)
    dress = products["فستان صيفي"]
    assert dress.price == Decimal("120.00")
    assert dress.category == "ملابس نسائية"
    assert dress.aliases == ["dress"]
    assert [v.name for v in dress.variants] == ["أحمر / M", "أحمر / L", "أزرق / M", "أزرق / L"]
    assert dress.variants[0].options == {"color": "أحمر", "size": "M"}
    assert dress.variants[3].quantity == 0  # out of stock still imports

    bag = products["حقيبة جلد"]
    assert [v.name for v in bag.variants] == [DEFAULT_VARIANT]
    assert bag.variants[0].options == {}
    assert bag.aliases == ["شنطة", "شنطه", "حقيبة يد"]

    perfume = products["عطر ورد"]
    assert [v.name for v in perfume.variants] == ["50ml", "100ml"]  # size only, no color

    jeans = products["بنطلون جينز"]
    assert [v.name for v in jeans.variants][:2] == ["أزرق / 30", "أزرق / 32"]


def test_search_text_is_normalized():
    plan = plan_from_file("x.csv", csv_bytes("product_name,price,quantity", "إيشارب حريرية,45,3"))
    assert plan.products[0].search_text == "ايشارب حريريه"


# --- bad rows are reported and skipped, the rest still imports ---------------

def test_bad_rows_reported_with_row_numbers_and_rest_imported():
    content = csv_bytes(
        "product_name,price,color,size,quantity",
        "فستان صيفي,120,أحمر,M,4",       # 2 ok
        "قميص,abc,أبيض,M,3",             # 3 price not a number
        "قميص,60,أبيض,L,-1",             # 4 negative quantity
        ",60,أبيض,L,2",                  # 5 no name
        "قميص,60,أسود,L,2.5",            # 6 fractional quantity
        "فستان صيفي,120,أحمر,M,1",       # 7 duplicate variant
        "فستان صيفي,99,أزرق,M,1",        # 8 price differs from row 2
        "قميص,60,أسود,M,6",              # 9 ok
        "حقيبة,90,,,",                   # 10 quantity empty
    )
    plan = plan_from_file("x.csv", content)

    errors = {e.row: e.message for e in plan.errors}
    assert sorted(errors) == [3, 4, 5, 6, 7, 8, 10]
    assert "السعر" in errors[3]
    assert "سالبة" in errors[4]
    assert "اسم المنتج" in errors[5]
    assert "صحيح" in errors[6]
    assert "مكرر" in errors[7]
    assert "variant_price" in errors[8]
    assert "الكمية" in errors[10]

    products = by_name(plan)
    assert set(products) == {"فستان صيفي", "قميص"}
    assert [v.row for v in products["قميص"].variants] == [9]


def test_variant_price_and_sku():
    content = csv_bytes(
        "product_name,price,size,quantity,variant_price,sku",
        "عطر,85,50ml,6,,P-50",
        "عطر,85,100ml,3,140,P-100",
        "عطر,85,200ml,1,250,P-100",   # duplicate SKU
    )
    plan = plan_from_file("x.csv", content)
    variants = plan.products[0].variants
    assert [v.price_override for v in variants] == [None, Decimal("140.00")]
    assert [v.sku for v in variants] == ["P-50", "P-100"]
    assert len(plan.errors) == 1 and plan.errors[0].row == 4


def test_same_product_with_different_spelling_groups_together():
    content = csv_bytes("product_name,price,color,quantity", "شنطة جلد,90,بني,2", "شنطه جلد,90,أسود,1")
    plan = plan_from_file("x.csv", content)
    assert len(plan.products) == 1
    assert len(plan.products[0].variants) == 2


def test_arabic_digits_and_arabic_headers():
    content = csv_bytes("اسم المنتج,السعر,المقاس,الكمية", "حذاء,٢٠٠,٤٢,٣")
    plan = plan_from_file("x.csv", content)
    variant = plan.products[0].variants[0]
    assert plan.products[0].price == Decimal("200.00")
    assert variant.name == "42" and variant.quantity == 3


def test_blank_lines_are_ignored_and_bom_is_handled():
    content = "﻿product_name,price,quantity\n\nوشاح,45,12\n,,\n".encode("utf-8")
    plan = plan_from_file("x.csv", content)
    assert plan.total_rows == 1 and plan.errors == []


# --- whole-file errors -------------------------------------------------------

@pytest.mark.parametrize("filename, content, message", [
    ("x.xls", b"anything", "غير مدعوم"),
    ("x.csv", b"", "فاضي"),
    ("x.csv", csv_bytes("product_name,price", "a,1"), "quantity"),
    ("x.csv", "product_name,price,quantity\nفستان,1,1".encode("cp1256"), "UTF-8"),
    ("x.xlsx", b"not really a zip", "Excel"),
])
def test_unusable_files_raise(filename, content, message):
    with pytest.raises(ImportFileError, match=message):
        plan_from_file(filename, content)


# --- xlsx --------------------------------------------------------------------

def test_xlsx_with_numeric_cells():
    wb = Workbook()
    ws = wb.active
    ws.append(["product_name", "price", "color", "size", "quantity"])
    ws.append(["بنطلون جينز", 150, "أزرق", 30, 5])        # numeric size and quantity
    ws.append(["بنطلون جينز", 150.0, "أسود", 32.0, 0.0])  # floats from Excel
    ws.append(["حقيبة جلد", 90.5, None, None, 7])
    buf = io.BytesIO()
    wb.save(buf)

    plan = plan_from_file("stock.xlsx", buf.getvalue())
    assert plan.errors == []
    products = by_name(plan)
    assert [v.name for v in products["بنطلون جينز"].variants] == ["أزرق / 30", "أسود / 32"]
    assert products["حقيبة جلد"].price == Decimal("90.50")
    assert products["حقيبة جلد"].variants[0].name == DEFAULT_VARIANT


def test_variant_name():
    assert variant_name({"color": "أحمر", "size": "M"}) == "أحمر / M"
    assert variant_name({"size": "M"}) == "M"
    assert variant_name({}) == DEFAULT_VARIANT
