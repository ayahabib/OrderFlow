"""Inventory import: preview a file, then confirm to save it.

The client uploads the same file to both endpoints; nothing is stored between
preview and confirm. store_id comes from the token only.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.api.auth import get_current_store_id
from app.db import products as products_db
from app.db.pool import get_db
from app.services import excel_import, inventory, pricing
from app.utils.arabic import normalize_arabic, to_latin_digits

router = APIRouter(prefix="/api/products", tags=["products"])
variants_router = APIRouter(prefix="/api/variants", tags=["products"])

# Money: numeric(10,2) in the DB -> Decimal with 2 places, never float.
Money = Annotated[Decimal, Field(ge=0, max_digits=10, decimal_places=2)]
Stock = Annotated[int, Field(ge=0, le=1_000_000)]


# --- products table ----------------------------------------------------------

def _annotate(variant: dict, product_id: UUID, offers: list[pricing.Offer], now: datetime) -> dict:
    price = pricing.variant_price(variant["effective_price"], product_id, variant["id"], offers, now)
    variant["offer_price"] = price.unit_price if price.offer else None
    variant["offer_name"] = price.offer.name if price.offer else None
    return variant


async def _with_offers(conn, store_id: UUID, products: list[dict]) -> list[dict]:
    offers, now = await pricing.load_offers(conn, store_id), datetime.now(timezone.utc)
    for p in products:
        for v in p["variants"]:
            _annotate(v, p["id"], offers, now)
    return products


class VariantOut(BaseModel):
    id: UUID
    name: str
    options: dict
    sku: str | None
    price_override: Decimal | None
    effective_price: Decimal  # coalesce(price_override, product.price), computed in SQL
    stock_quantity: int
    low_stock_threshold: int
    is_active: bool
    # Price after the best live offer (services/pricing.py); None when no offer applies.
    offer_price: Decimal | None = None
    offer_name: str | None = None


class ProductOut(BaseModel):
    id: UUID
    name: str
    category: str | None
    price: Decimal
    is_active: bool
    variants: list[VariantOut]


class ProductUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    price: Money


class VariantUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stock_quantity: Stock | None = None
    price_override: Money | None = None  # explicit null clears the override
    low_stock_threshold: Annotated[int, Field(ge=0, le=100_000)] | None = None  # alert level (3.2)


OptionText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=50)]


class VariantIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    color: OptionText | None = None
    size: OptionText | None = None
    quantity: Stock
    price_override: Money | None = None
    sku: OptionText | None = None

    def to_new(self) -> inventory.NewVariant:
        options = {k: to_latin_digits(v) for k, v in (("color", self.color), ("size", self.size)) if v}
        return inventory.NewVariant(
            options=options, quantity=self.quantity, price_override=self.price_override, sku=self.sku or None,
        )


class ProductCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")  # no store_id from the body
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    price: Money
    category: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] | None = None
    variants: Annotated[list[VariantIn], Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def unique_variants(self):
        names = [normalize_arabic(excel_import.variant_name(v.to_new().options)) for v in self.variants]
        if len(names) != len(set(names)):
            raise ValueError("في نسختين بنفس اللون والمقاس")
        return self


async def _product_out(conn, store_id: UUID, product_id: UUID) -> dict:
    products = await _with_offers(conn, store_id, await products_db.list_products(conn, store_id))
    return next(p for p in products if p["id"] == product_id)


@router.post("", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
async def create_product(
    body: ProductCreate,
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> dict:
    """Add a product by hand. Opening stock records a 'manual' movement per variant."""
    try:
        product_id = await inventory.create_product(
            conn, store_id, name=" ".join(body.name.split()), price=body.price,
            category=body.category or None, description=body.description or None,
            variants=[v.to_new() for v in body.variants],
        )
    except inventory.DuplicateError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc))
    return await _product_out(conn, store_id, product_id)


@router.post("/{product_id}/variants", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
async def add_variant(
    product_id: UUID,
    body: VariantIn,
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> dict:
    """Add a new color/size to an existing product."""
    try:
        variant_id = await inventory.add_variant(conn, store_id, product_id, body.to_new())
    except inventory.DuplicateError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc))
    if variant_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="المنتج غير موجود")
    return await _product_out(conn, store_id, product_id)


@router.get("", response_model=list[ProductOut])
async def list_products(
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> list[dict]:
    return await _with_offers(conn, store_id, await products_db.list_products(conn, store_id))


@router.patch("/{product_id}", response_model=ProductOut)
async def update_product(
    product_id: UUID,
    body: ProductUpdate,
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> dict:
    if not await inventory.update_product_price(conn, store_id, product_id, body.price):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="المنتج غير موجود")
    return await _product_out(conn, store_id, product_id)


@variants_router.patch("/{variant_id}", response_model=VariantOut)
async def update_variant(
    variant_id: UUID,
    body: VariantUpdate,
    request: Request,
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> dict:
    """Edit quantity and/or price override. A quantity change records a 'manual' movement."""
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="ما في تعديل")
    if "stock_quantity" in fields and fields["stock_quantity"] is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="الكمية لازم تكون رقم")
    if "low_stock_threshold" in fields and fields["low_stock_threshold"] is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="حد التنبيه لازم يكون رقم")

    kwargs = {}
    if "stock_quantity" in fields:
        kwargs["stock_quantity"] = fields["stock_quantity"]
    if "price_override" in fields:
        kwargs["price_override"] = fields["price_override"]
    if "low_stock_threshold" in fields:
        kwargs["low_stock_threshold"] = fields["low_stock_threshold"]
    old = await products_db.get_variant(conn, store_id, variant_id)
    if not await inventory.update_variant(conn, store_id, variant_id, **kwargs):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="النسخة غير موجودة")
    variant = await products_db.get_variant(conn, store_id, variant_id)
    request.state.audit = {"before": {k: old[k] for k in kwargs}, "after": {k: variant[k] for k in kwargs}}   # 3.14
    offers = await pricing.load_offers(conn, store_id)
    return _annotate(variant, variant["product_id"], offers, datetime.now(timezone.utc))


# --- import ------------------------------------------------------------------

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


# --- delete ------------------------------------------------------------------

@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    product_id: UUID,
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> None:
    if not await inventory.delete_product(conn, store_id, product_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="المنتج غير موجود")


@variants_router.delete("/{variant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_variant(
    variant_id: UUID,
    store_id: UUID = Depends(get_current_store_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> None:
    try:
        found = await inventory.delete_variant(conn, store_id, variant_id)
    except inventory.LastVariantError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc))
    if not found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="النسخة غير موجودة")
