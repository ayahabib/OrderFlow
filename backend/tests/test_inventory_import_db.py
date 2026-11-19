"""apply_import against the real database (rolled-back transaction)."""

from pathlib import Path

import pytest

from app.services.excel_import import plan_from_file
from app.services.inventory import ImportConflictError, apply_import
from tests.authutil import insert_auth_user

pytestmark = pytest.mark.db

SAMPLE = Path(__file__).resolve().parents[2] / "eval" / "sample_inventory.csv"


async def new_store(conn) -> object:
    owner = await insert_auth_user(conn)
    return await conn.fetchval("insert into stores (owner_id, name) values ($1, 'متجر') returning id", owner)


def csv_plan(*lines):
    return plan_from_file("x.csv", ("\n".join(lines) + "\n").encode("utf-8"))


async def test_sample_import_creates_products_variants_and_movements(db_tx):
    store = await new_store(db_tx)
    result = await apply_import(db_tx, store, plan_from_file("s.csv", SAMPLE.read_bytes()))

    assert (result.products_created, result.variants_created) == (7, 21)
    assert await db_tx.fetchval("select count(*) from products where store_id = $1", store) == 7
    assert await db_tx.fetchval("select count(*) from product_variants where store_id = $1", store) == 21

    # One 'upload' movement per variant with stock > 0 (two rows in the sample are 0).
    movements = await db_tx.fetch(
        "select change, reason from inventory_movements where store_id = $1", store
    )
    assert len(movements) == 19 == result.movements
    assert {m["reason"] for m in movements} == {"upload"}
    total_stock = await db_tx.fetchval("select sum(stock_quantity) from product_variants where store_id = $1", store)
    assert sum(m["change"] for m in movements) == total_stock

    bag = await db_tx.fetchrow(
        """select p.search_text, p.aliases, v.name, v.options::text as options, v.stock_quantity
             from products p join product_variants v on v.product_id = p.id
            where p.store_id = $1 and p.name = 'حقيبة جلد'""",
        store,
    )
    assert bag["name"] == "الافتراضي" and bag["stock_quantity"] == 7
    assert bag["options"] == "{}"
    assert bag["search_text"] == "حقيبه جلد"
    assert bag["aliases"] == ["شنطة", "شنطه", "حقيبة يد"]


async def test_reupload_updates_quantity_and_records_delta(db_tx):
    store = await new_store(db_tx)
    await apply_import(db_tx, store, csv_plan(
        "product_name,price,color,size,quantity", "فستان صيفي,120,أحمر,M,4", "فستان صيفي,120,أحمر,L,2",
    ))
    # Same product spelled differently, new price, one quantity changed, one variant added.
    result = await apply_import(db_tx, store, csv_plan(
        "product_name,price,color,size,quantity",
        "فستان صيفى,130,أحمر,M,10", "فستان صيفى,130,أحمر,L,2", "فستان صيفى,130,أزرق,M,5",
    ))
    assert (result.products_created, result.products_updated) == (0, 1)
    assert (result.variants_created, result.variants_updated) == (1, 2)

    rows = await db_tx.fetch(
        """select v.name, v.stock_quantity, p.price, p.name as product
             from product_variants v join products p on p.id = v.product_id
            where v.store_id = $1 order by v.name""",
        store,
    )
    assert {r["name"]: r["stock_quantity"] for r in rows} == {"أحمر / M": 10, "أحمر / L": 2, "أزرق / M": 5}
    assert {r["product"] for r in rows} == {"فستان صيفي"}  # kept the original name, no duplicate
    assert {str(r["price"]) for r in rows} == {"130.00"}

    changes = await db_tx.fetch(
        """select v.name, m.change from inventory_movements m join product_variants v on v.id = m.variant_id
            where m.store_id = $1 order by m.created_at, v.name""",
        store,
    )
    # First upload: +4, +2. Second: +6 (4 -> 10), +5 (new). Unchanged L records nothing.
    assert sorted((c["name"], c["change"]) for c in changes) == sorted(
        [("أحمر / M", 4), ("أحمر / L", 2), ("أحمر / M", 6), ("أزرق / M", 5)]
    )


async def test_import_only_touches_own_store(db_tx):
    store_a, store_b = await new_store(db_tx), await new_store(db_tx)
    await apply_import(db_tx, store_b, csv_plan("product_name,price,quantity", "وشاح حرير,45,12"))
    await apply_import(db_tx, store_a, csv_plan("product_name,price,quantity", "وشاح حرير,50,1"))

    b = await db_tx.fetchrow(
        """select p.price, v.stock_quantity from products p join product_variants v on v.product_id = p.id
            where p.store_id = $1""",
        store_b,
    )
    assert (str(b["price"]), b["stock_quantity"]) == ("45.00", 12)


async def test_sku_conflict_rolls_back_everything(db_tx):
    store = await new_store(db_tx)
    await apply_import(db_tx, store, csv_plan("product_name,price,quantity,sku", "حقيبة,90,7,BAG-1"))

    with pytest.raises(ImportConflictError, match="SKU"):
        await apply_import(db_tx, store, csv_plan(
            "product_name,price,quantity,sku", "وشاح,45,3,", "قميص,60,2,BAG-1",
        ))
    names = [r["name"] for r in await db_tx.fetch("select name from products where store_id = $1", store)]
    assert names == ["حقيبة"]  # the earlier valid row in the failed file was rolled back too
