"""Inventory import from Excel/CSV (DATABASE.md section 4). Pure code, no DB, no LLM.

Steps: read the file into raw rows -> validate each row -> group rows into
products with variants. Bad rows get a clear Arabic error and are skipped;
the other rows still import.
"""

import csv
import io
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from app.utils.arabic import normalize_arabic, to_latin_digits

DEFAULT_VARIANT = "الافتراضي"
MAX_ROWS = 5000

# Accepted header names (English from DATABASE.md, plus common Arabic ones) -> field.
HEADER_ALIASES = {
    "product_name": "product_name", "product": "product_name", "name": "product_name",
    "اسم المنتج": "product_name", "المنتج": "product_name",
    "price": "price", "السعر": "price",
    "quantity": "quantity", "qty": "quantity", "الكمية": "quantity",
    "color": "color", "colour": "color", "اللون": "color",
    "size": "size", "المقاس": "size", "الحجم": "size",
    "variant_price": "variant_price", "سعر خاص": "variant_price", "سعر النسخة": "variant_price",
    "sku": "sku",
    "category": "category", "التصنيف": "category",
    "description": "description", "الوصف": "description",
    "aliases": "aliases", "أسماء بديلة": "aliases",
}
REQUIRED_COLUMNS = ("product_name", "price", "quantity")
# Option columns in the order they appear in the variant name ("أحمر / M").
OPTION_COLUMNS = ("color", "size")


class ImportFileError(ValueError):
    """The whole file can't be used (wrong type, missing columns, too many rows)."""


@dataclass
class RowError:
    row: int  # spreadsheet row number (header is row 1)
    message: str


@dataclass
class VariantPlan:
    name: str
    options: dict
    quantity: int
    price_override: Decimal | None
    sku: str | None
    row: int


@dataclass
class ProductPlan:
    name: str
    search_text: str
    price: Decimal
    category: str | None
    description: str | None
    aliases: list[str]
    first_row: int
    variants: list[VariantPlan] = field(default_factory=list)


@dataclass
class ImportPlan:
    products: list[ProductPlan]
    errors: list[RowError]
    total_rows: int

    @property
    def variant_count(self) -> int:
        return sum(len(p.variants) for p in self.products)


# --- reading files ---------------------------------------------------------

def _header_key(value) -> str | None:
    key = str(value or "").strip().lower()
    return HEADER_ALIASES.get(key) or HEADER_ALIASES.get(str(value or "").strip())


def read_rows(filename: str, content: bytes) -> list[tuple[int, dict]]:
    """Return [(row_number, {field: raw value})] for non-empty data rows."""
    name = filename.lower()
    if name.endswith(".csv"):
        table = _read_csv(content)
    elif name.endswith(".xlsx"):
        table = _read_xlsx(content)
    else:
        raise ImportFileError("نوع الملف غير مدعوم. ارفع ملف .xlsx أو .csv")

    if not table:
        raise ImportFileError("الملف فاضي")

    header = [_header_key(h) for h in table[0]]
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        raise ImportFileError(f"أعمدة ناقصة: {', '.join(missing)}")

    rows = []
    for index, values in enumerate(table[1:], start=2):
        record = {}
        for key, value in zip(header, values):
            if key and key not in record:  # first column wins if a header repeats
                record[key] = value
        if any(_clean(v) for v in record.values()):
            rows.append((index, record))
    if len(rows) > MAX_ROWS:
        raise ImportFileError(f"الملف فيه أكتر من {MAX_ROWS} صف")
    return rows


def _read_csv(content: bytes) -> list[list]:
    try:
        text = content.decode("utf-8-sig")  # Excel adds a BOM when saving CSV as UTF-8
    except UnicodeDecodeError:
        raise ImportFileError("ملف CSV لازم يكون بترميز UTF-8")
    return [row for row in csv.reader(io.StringIO(text))]


def _read_xlsx(content: bytes) -> list[list]:
    from openpyxl import load_workbook  # imported lazily: only needed for xlsx

    try:
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception:
        raise ImportFileError("ما قدرنا نقرأ ملف Excel. تأكد إنه .xlsx سليم")
    try:
        ws = wb.worksheets[0]
        table = []
        for row in ws.iter_rows(values_only=True):
            table.append(list(row))
            if len(table) > MAX_ROWS + 1:
                raise ImportFileError(f"الملف فيه أكتر من {MAX_ROWS} صف")
        return table
    finally:
        wb.close()


# --- validating values -----------------------------------------------------

def _clean(value) -> str:
    """Cell value as trimmed text. Excel numbers like 30.0 become "30"."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def _parse_money(raw: str, label: str) -> Decimal:
    text = to_latin_digits(raw).replace(",", "")
    try:
        amount = Decimal(text)
    except InvalidOperation:
        raise ValueError(f"{label} لازم يكون رقم (القيمة: {raw})")
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"{label} لازم يكون رقم موجب (القيمة: {raw})")
    if amount != amount.quantize(Decimal("0.01")):
        raise ValueError(f"{label} فيه أكتر من خانتين عشريتين (القيمة: {raw})")
    if amount >= Decimal("100000000"):
        raise ValueError(f"{label} كبير كتير (القيمة: {raw})")
    return amount.quantize(Decimal("0.01"))


def _parse_quantity(raw: str) -> int:
    text = to_latin_digits(raw)
    try:
        number = Decimal(text)
    except InvalidOperation:
        raise ValueError(f"الكمية لازم تكون رقم صحيح (القيمة: {raw})")
    if not number.is_finite() or number != number.to_integral_value():
        raise ValueError(f"الكمية لازم تكون رقم صحيح (القيمة: {raw})")
    if number < 0:
        raise ValueError(f"الكمية ما بتكون سالبة (القيمة: {raw})")
    if number > 1_000_000:
        raise ValueError(f"الكمية كبيرة كتير (القيمة: {raw})")
    return int(number)


@dataclass
class _ValidRow:
    row: int
    product_name: str
    price: Decimal
    quantity: int
    options: dict
    variant_price: Decimal | None
    sku: str | None
    category: str | None
    description: str | None
    aliases: list[str]


def _validate(row: int, record: dict) -> _ValidRow:
    """Raise ValueError with an Arabic message for the first problem found."""
    name = " ".join(_clean(record.get("product_name")).split())
    if not name:
        raise ValueError("اسم المنتج فاضي")
    if len(name) > 200:
        raise ValueError("اسم المنتج طويل كتير")

    raw_price = _clean(record.get("price"))
    if not raw_price:
        raise ValueError("السعر فاضي")
    price = _parse_money(raw_price, "السعر")

    raw_qty = _clean(record.get("quantity"))
    if not raw_qty:
        raise ValueError("الكمية فاضية")
    quantity = _parse_quantity(raw_qty)

    raw_vprice = _clean(record.get("variant_price"))
    variant_price = _parse_money(raw_vprice, "السعر الخاص") if raw_vprice else None

    options = {}
    for col in OPTION_COLUMNS:
        value = to_latin_digits(_clean(record.get(col)))
        if value:
            options[col] = value

    aliases = [a.strip() for a in _clean(record.get("aliases")).split("|") if a.strip()]

    return _ValidRow(
        row=row, product_name=name, price=price, quantity=quantity, options=options,
        variant_price=variant_price, sku=_clean(record.get("sku")) or None,
        category=_clean(record.get("category")) or None,
        description=_clean(record.get("description")) or None,
        aliases=aliases,
    )


def variant_name(options: dict) -> str:
    """"أحمر / M" from the filled options, or "الافتراضي" when there are none."""
    parts = [options[c] for c in OPTION_COLUMNS if options.get(c)]
    return " / ".join(parts) if parts else DEFAULT_VARIANT


# --- grouping --------------------------------------------------------------

def build_plan(rows: list[tuple[int, dict]]) -> ImportPlan:
    """Validate rows and group them into products. Never raises for a bad row."""
    errors: list[RowError] = []
    products: dict[str, ProductPlan] = {}  # keyed by normalized name
    seen_variants: dict[tuple[str, str], int] = {}
    seen_skus: dict[str, int] = {}

    for row_number, record in rows:
        try:
            r = _validate(row_number, record)
        except ValueError as exc:
            errors.append(RowError(row_number, str(exc)))
            continue

        key = normalize_arabic(r.product_name)
        product = products.get(key)
        if product is not None and r.price != product.price:
            errors.append(RowError(
                r.row,
                f"سعر \"{r.product_name}\" ({r.price}) مختلف عن الصف {product.first_row} ({product.price}). "
                "للسعر المختلف استخدم عمود variant_price",
            ))
            continue

        vname = variant_name(r.options)
        vkey = (key, normalize_arabic(vname))
        if vkey in seen_variants:
            errors.append(RowError(r.row, f"\"{r.product_name} - {vname}\" مكرر (نفس الصف {seen_variants[vkey]})"))
            continue
        if r.sku and r.sku in seen_skus:
            errors.append(RowError(r.row, f"SKU \"{r.sku}\" مكرر (نفس الصف {seen_skus[r.sku]})"))
            continue

        if product is None:
            # Category, description and aliases are read from the product's first row.
            product = ProductPlan(
                name=r.product_name, search_text=key, price=r.price, category=r.category,
                description=r.description, aliases=r.aliases, first_row=r.row,
            )
            products[key] = product

        seen_variants[vkey] = r.row
        if r.sku:
            seen_skus[r.sku] = r.row
        product.variants.append(VariantPlan(
            name=vname, options=r.options, quantity=r.quantity,
            price_override=r.variant_price, sku=r.sku, row=r.row,
        ))

    return ImportPlan(products=list(products.values()), errors=errors, total_rows=len(rows))


def plan_from_file(filename: str, content: bytes) -> ImportPlan:
    return build_plan(read_rows(filename, content))
