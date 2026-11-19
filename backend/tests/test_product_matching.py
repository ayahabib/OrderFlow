"""Product + variant matching (TASKS.md 1.7)."""

import json
import uuid
from pathlib import Path

import pytest

from app.services.excel_import import plan_from_file
from app.services.inventory import apply_import
from app.services.product_matching import MatchResult, clarifying_question, match_item, pick_variant
from app.utils.arabic import normalize_arabic
from tests.authutil import insert_auth_user

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "eval" / "sample_inventory.csv"
TESTSET = ROOT / "eval" / "extraction_testset.jsonl"


async def store_with_sample(conn):
    owner = await insert_auth_user(conn)
    store = await conn.fetchval("insert into stores (owner_id, name) values ($1, 'متجر') returning id", owner)
    await apply_import(conn, store, plan_from_file("s.csv", SAMPLE.read_bytes()))
    return store


def expected_items():
    for line in TESTSET.read_text(encoding="utf-8").splitlines():
        r = json.loads(line) if line.strip() else None
        if r and r["expected_match"]:
            for item, match in zip(r["expected"]["items"], r["expected_match"]):
                yield r["id"], item, match


# --- the whole test set, with the correct extraction as input ------------------

@pytest.mark.db
async def test_every_expected_match_in_the_test_set(db_tx):
    store = await store_with_sample(db_tx)
    misses = []
    count = 0
    for rid, item, exp in expected_items():
        count += 1
        got = await match_item(db_tx, store, item["product_text"], item["color"], item["size"], item["quantity"])
        actual = {"status": got.status, "product": got.product_name if got.status != "not_found" else None,
                  "variant": got.variant_name, "stock_ok": got.stock_ok}
        if actual != exp:
            misses.append((rid, item["product_text"], exp, actual))
    assert count == 27
    assert misses == []


# --- the four TASKS.md cases, plus edge cases -----------------------------------

@pytest.mark.db
async def test_matched_typo_missing_variant_not_found(db_tx):
    store = await store_with_sample(db_tx)

    exact = await match_item(db_tx, store, "فستان صيفي", "أحمر", "M")
    assert (exact.status, exact.variant_name, exact.stock_ok) == ("matched", "أحمر / M", True)

    typo = await match_item(db_tx, store, "فسطان صيفي", "احمر", "L")   # typo + color without hamza
    assert (typo.status, typo.product_name, typo.variant_name) == ("matched", "فستان صيفي", "أحمر / L")

    missing = await match_item(db_tx, store, "جينز", "أزرق", None)
    assert (missing.status, missing.product_name, missing.missing_options) == ("needs_variant", "بنطلون جينز", ["size"])
    assert missing.option_values == {"size": ["30", "32"]}
    assert clarifying_question(missing) == "أي المقاس (30، 32) بدك من بنطلون جينز؟"

    absent = await match_item(db_tx, store, "طقم رياضي")
    assert absent.status == "not_found"   # not "حذاء رياضي" via the shared word "رياضي"
    assert "مش موجود" in clarifying_question(absent)


@pytest.mark.db
async def test_nothing_specified_asks_for_both_options(db_tx):
    store = await store_with_sample(db_tx)
    r = await match_item(db_tx, store, "فستان")
    assert (r.status, r.missing_options) == ("needs_variant", ["color", "size"])
    assert r.option_values == {"color": ["أحمر", "أزرق"], "size": ["L", "M"]}
    assert clarifying_question(r) == "أي اللون (أحمر، أزرق) والمقاس (L، M) بدك من فستان صيفي؟"


@pytest.mark.db
async def test_aliases_match_with_any_spelling(db_tx):
    store = await store_with_sample(db_tx)
    for text in ("شنطة", "شنطه", "حقيبة يد", "الشنطة"):
        r = await match_item(db_tx, store, text)
        assert (r.status, r.product_name, r.variant_name) == ("matched", "حقيبة جلد", "الافتراضي"), text
    assert (await match_item(db_tx, store, "ايشارب")).product_name == "وشاح حرير"   # alias "إيشارب"


@pytest.mark.db
async def test_combination_that_does_not_exist(db_tx):
    store = await store_with_sample(db_tx)
    r = await match_item(db_tx, store, "فستان صيفي", "أخضر", "M")
    assert r.status == "variant_unavailable"
    assert r.option_values == {"color": ["أحمر", "أزرق"], "size": ["L", "M"]}
    assert "مش متوفر" in clarifying_question(r)


@pytest.mark.db
async def test_out_of_stock_and_short_stock(db_tx):
    store = await store_with_sample(db_tx)
    zero = await match_item(db_tx, store, "فستان صيفي", "أزرق", "L", 1)         # stock 0
    assert (zero.status, zero.stock_ok) == ("matched", False)
    assert "خلص" in clarifying_question(zero)
    short = await match_item(db_tx, store, "حذاء رياضي", "أبيض", "42", 5)       # stock 2
    assert (short.stock_ok, short.stock_quantity) == (False, 2)
    assert "2 بس" in clarifying_question(short)


@pytest.mark.db
async def test_two_similar_products_ask_which_one(db_tx):
    store = await store_with_sample(db_tx)
    await apply_import(db_tx, store, plan_from_file("x.csv", "product_name,price,quantity\nفستان سهرة,250,2\n".encode()))
    r = await match_item(db_tx, store, "فستان")
    assert r.status == "ambiguous_product"
    assert set(r.candidates) == {"فستان صيفي", "فستان سهرة"}
    assert clarifying_question(r).startswith("قصدك أي واحد")


@pytest.mark.db
async def test_inactive_products_and_variants_are_skipped(db_tx):
    store = await store_with_sample(db_tx)
    await db_tx.execute("update products set is_active = false where store_id = $1 and name = 'وشاح حرير'", store)
    assert (await match_item(db_tx, store, "شال")).status == "not_found"
    await db_tx.execute(
        """update product_variants set is_active = false
            where store_id = $1 and name = 'أحمر / M'""", store)
    assert (await match_item(db_tx, store, "فستان صيفي", "أحمر", "M")).status == "variant_unavailable"


@pytest.mark.db
async def test_matching_only_sees_own_store(db_tx):
    store_a = await store_with_sample(db_tx)
    owner_b = await insert_auth_user(db_tx)
    store_b = await db_tx.fetchval("insert into stores (owner_id, name) values ($1, 'ب') returning id", owner_b)
    assert (await match_item(db_tx, store_b, "فستان صيفي", "أحمر", "M")).status == "not_found"
    assert (await match_item(db_tx, store_a, "فستان صيفي", "أحمر", "M")).status == "matched"


@pytest.mark.db
async def test_sql_and_python_normalizers_agree(db_tx):
    samples = ["أحمر", "إيشارب", "آخر", "شنطة", "مستشفى", "فُسْتَانٌ", "فســـتان", "  فستان   صيفي ",
               "مسؤول", "قائمة", "Dress", "مقاس ٤٢", "۵۰ml", "شو السعر؟", ""]
    for s in samples:
        assert await db_tx.fetchval("select normalize_ar($1)", s) == normalize_arabic(s), s


# --- pure helpers (no database) ---------------------------------------------------

def v(name, **options):
    return {"id": uuid.uuid4(), "name": name, "options": options, "stock_quantity": 1}


def test_pick_variant_rules():
    variants = [v("أحمر / M", color="أحمر", size="M"), v("أحمر / L", color="أحمر", size="L"),
                v("أزرق / M", color="أزرق", size="M")]
    assert pick_variant(variants, "احمر", "m")[0] == "matched"            # spelling and case don't matter
    assert pick_variant(variants, "أحمر", None)[2] == ["size"]
    assert pick_variant(variants, None, "M")[2] == ["color"]
    assert pick_variant(variants, "أخضر", None)[0] == "variant_unavailable"
    default = [v("الافتراضي")]
    assert pick_variant(default, "أسود", None)[0] == "matched"            # bag has no colors: option ignored
    assert pick_variant([], None, None)[0] == "variant_unavailable"


def test_no_question_when_matched_and_in_stock():
    r = MatchResult(status="matched", product_name="قميص قطن", variant_name="أبيض / M", stock_quantity=10, stock_ok=True)
    assert clarifying_question(r) is None
