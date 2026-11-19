"""Import endpoints: preview saves nothing, confirm saves valid rows, bad uploads are rejected."""

from pathlib import Path

import pytest

from tests.authutil import bearer, client_for, insert_auth_user, make_token, patch_jwks

SAMPLE = Path(__file__).resolve().parents[2] / "eval" / "sample_inventory.csv"
WITH_BAD_ROW = (
    "product_name,price,color,size,quantity\n"
    "فستان صيفي,120,أحمر,M,4\n"
    "قميص,abc,أبيض,M,3\n"
    "حقيبة جلد,90,,,7\n"
).encode("utf-8")


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch):
    patch_jwks(monkeypatch)


async def owner_with_store(conn):
    user_id = await insert_auth_user(conn)
    store_id = await conn.fetchval("insert into stores (owner_id, name) values ($1, 'متجر') returning id", user_id)
    return bearer(make_token(user_id)), store_id


def upload(name: str, content: bytes):
    return {"file": (name, content, "text/csv")}


@pytest.mark.db
async def test_preview_then_confirm_with_a_bad_row(db_tx):
    h, store_id = await owner_with_store(db_tx)
    async with client_for(db_tx) as c:
        r = await c.post("/api/products/import/preview", headers=h, files=upload("stock.csv", WITH_BAD_ROW))
        assert r.status_code == 200
        preview = r.json()
        assert preview["total_rows"] == 3 and preview["valid_rows"] == 2
        assert preview["errors"] == [{"row": 3, "message": preview["errors"][0]["message"]}]
        assert "السعر" in preview["errors"][0]["message"]
        assert [p["name"] for p in preview["products"]] == ["فستان صيفي", "حقيبة جلد"]
        assert all(p["is_new"] for p in preview["products"])
        assert preview["products"][1]["variants"][0]["name"] == "الافتراضي"

        # Preview wrote nothing.
        assert await db_tx.fetchval("select count(*) from products where store_id = $1", store_id) == 0

        r = await c.post("/api/products/import/confirm", headers=h, files=upload("stock.csv", WITH_BAD_ROW))
        assert r.status_code == 200
        result = r.json()
        assert (result["products_created"], result["variants_created"], result["movements"]) == (2, 2, 2)
        assert [e["row"] for e in result["skipped_rows"]] == [3]

        # A second preview now shows current quantities.
        again = (await c.post("/api/products/import/preview", headers=h, files=upload("s.csv", WITH_BAD_ROW))).json()
        dress = again["products"][0]
        assert dress["is_new"] is False
        assert dress["variants"][0]["current_quantity"] == 4


@pytest.mark.db
async def test_confirm_sample_file(db_tx):
    h, store_id = await owner_with_store(db_tx)
    async with client_for(db_tx) as c:
        r = await c.post("/api/products/import/confirm", headers=h, files=upload("s.csv", SAMPLE.read_bytes()))
    assert r.status_code == 200
    assert r.json()["variants_created"] == 21
    assert r.json()["skipped_rows"] == []


@pytest.mark.db
@pytest.mark.parametrize("name, content, code", [
    ("stock.xls", b"old excel", 422),
    ("stock.csv", b"product_name,price\nx,1\n", 422),              # missing quantity column
    ("stock.csv", b"product_name,price,quantity\n,abc,\n", 422),  # no valid rows (confirm only)
    ("big.csv", b"x" * (2 * 1024 * 1024 + 1), 413),
])
async def test_confirm_rejects_bad_uploads(db_tx, name, content, code):
    h, store_id = await owner_with_store(db_tx)
    async with client_for(db_tx) as c:
        r = await c.post("/api/products/import/confirm", headers=h, files=upload(name, content))
    assert r.status_code == code
    assert isinstance(r.json()["detail"], str)
    assert await db_tx.fetchval("select count(*) from products where store_id = $1", store_id) == 0


class NoDb:
    """Auth runs before any DB access, so these never touch the connection."""


async def test_import_requires_login():
    async with client_for(NoDb()) as c:
        for path in ("/api/products/import/preview", "/api/products/import/confirm"):
            r = await c.post(path, files=upload("s.csv", WITH_BAD_ROW))
            assert r.status_code == 401
