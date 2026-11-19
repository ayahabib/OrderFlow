"""Inventory import: preview a file, then confirm to save it.

The client uploads the same file to both endpoints; nothing is stored between
preview and confirm. store_id comes from the token only.
"""

from decimal import Decimal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from pydantic import BaseModel

from app.api.auth import get_current_store_id
from app.db.pool import get_db
from app.services import excel_import, inventory
from app.utils.arabic import normalize_arabic

router = APIRouter(prefix="/api/products", tags=["products"])

MAX_UPLOAD_BYTES = 2 * 1024 * 1024  # 2 MB is plenty for a few thousand rows


class RowErrorOut(BaseModel):
    row: int
    message: str


class VariantPreview(BaseModel):
    row: int
    name: str
    quantity: int
    current_quantity: int | None  # None = new variant
    price_override: Decimal | None
    sku: str | None


class ProductPreview(BaseModel):
    name: str
    price: Decimal
    category: str | None
    is_new: bool
    variants: list[VariantPreview]


class ImportPreview(BaseModel):
    total_rows: int
    valid_rows: int
    products: list[ProductPreview]
    errors: list[RowErrorOut]


class ImportConfirmResult(BaseModel):
    products_created: int
    products_updated: int
    variants_created: int
    variants_updated: int
    movements: int
    skipped_rows: list[RowErrorOut]


async def _read_plan(file: UploadFile) -> excel_import.ImportPlan:
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="الملف أكبر من 2 ميغابايت")
    try:
        return excel_import.plan_from_file(file.filename or "", content)
    except excel_import.ImportFileError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


def _errors(plan: excel_import.ImportPlan) -> list[RowErrorOut]:
    return [RowErrorOut(row=e.row, message=e.message) for e in plan.errors]


@router.post("/import/preview", response_model=ImportPreview)
async def preview_import(
    file: UploadFile,
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> ImportPreview:
    """Parse and validate the file, and show what would change. Saves nothing."""
    plan = await _read_plan(file)
    stock = await inventory.existing_stock(conn, store_id)

    products = []
    for p in plan.products:
        current = stock.get(p.search_text)
        products.append(ProductPreview(
            name=p.name, price=p.price, category=p.category, is_new=current is None,
            variants=[
                VariantPreview(
                    row=v.row, name=v.name, quantity=v.quantity,
                    current_quantity=(current or {}).get(normalize_arabic(v.name)),
                    price_override=v.price_override, sku=v.sku,
                )
                for v in p.variants
            ],
        ))
    return ImportPreview(
        total_rows=plan.total_rows, valid_rows=plan.variant_count, products=products, errors=_errors(plan),
    )


@router.post("/import/confirm", response_model=ImportConfirmResult)
async def confirm_import(
    file: UploadFile,
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> ImportConfirmResult:
    """Save the valid rows (one transaction). Rows with errors are skipped and returned."""
    plan = await _read_plan(file)
    if not plan.products:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="ما في ولا صف صالح بالملف")
    try:
        result = await inventory.apply_import(conn, store_id, plan)
    except inventory.ImportConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc))
    return ImportConfirmResult(**result.__dict__, skipped_rows=_errors(plan))
