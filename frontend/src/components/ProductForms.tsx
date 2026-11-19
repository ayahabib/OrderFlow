import { useState, type FormEvent } from 'react'
import { validatePrice, validateQuantity } from './EditableCell'
import { addVariant, createProduct, type Product, type VariantInput } from '../lib/api'

type VariantRow = { color: string; size: string; quantity: string; price: string }
const emptyRow = (): VariantRow => ({ color: '', size: '', quantity: '0', price: '' })

/** Check one variant row and convert it for the API. Throws an Arabic message. */
function toVariantInput(row: VariantRow, index?: number): VariantInput {
  const where = index === undefined ? '' : ` (نسخة ${index + 1})`
  const qtyProblem = validateQuantity(row.quantity.trim())
  if (qtyProblem) throw new Error(`الكمية: ${qtyProblem}${where}`)
  const priceProblem = validatePrice(row.price.trim(), true)
  if (priceProblem) throw new Error(`السعر الخاص: ${priceProblem}${where}`)
  return {
    color: row.color.trim() || null,
    size: row.size.trim() || null,
    quantity: Number(row.quantity.trim()),
    price_override: row.price.trim() || null,
  }
}

function VariantFields({ row, onChange }: { row: VariantRow; onChange: (row: VariantRow) => void }) {
  return (
    <>
      <input aria-label="اللون" placeholder="اللون (اختياري)" value={row.color}
        onChange={(e) => onChange({ ...row, color: e.target.value })} />
      <input aria-label="المقاس" placeholder="المقاس (اختياري)" dir="auto" value={row.size}
        onChange={(e) => onChange({ ...row, size: e.target.value })} />
      <input aria-label="الكمية" placeholder="الكمية" dir="ltr" inputMode="numeric" className="narrow" value={row.quantity}
        onChange={(e) => onChange({ ...row, quantity: e.target.value })} />
      <input aria-label="سعر خاص" placeholder="سعر خاص" dir="ltr" inputMode="decimal" className="narrow" value={row.price}
        onChange={(e) => onChange({ ...row, price: e.target.value })} />
    </>
  )
}

/** New product with one or more variants. No color/size = a single "الافتراضي" variant. */
export function NewProductForm({ onCreated, onCancel }: { onCreated: (p: Product) => void; onCancel: () => void }) {
  const [name, setName] = useState('')
  const [price, setPrice] = useState('')
  const [category, setCategory] = useState('')
  const [rows, setRows] = useState<VariantRow[]>([emptyRow()])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    try {
      const priceProblem = validatePrice(price.trim())
      if (priceProblem) throw new Error(`السعر: ${priceProblem}`)
      const variants = rows.map((r, i) => toVariantInput(r, rows.length > 1 ? i : undefined))
      setBusy(true)
      onCreated(await createProduct({ name: name.trim(), price: price.trim(), category: category.trim() || null, variants }))
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="card wide" onSubmit={submit}>
      <h2>منتج جديد</h2>
      <div className="form-grid">
        <label>اسم المنتج<input required maxLength={200} value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label>السعر الأساسي<input required dir="ltr" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} /></label>
        <label>التصنيف <span className="hint">(اختياري)</span><input maxLength={100} value={category} onChange={(e) => setCategory(e.target.value)} /></label>
      </div>

      <strong>النسخ (الألوان والمقاسات)</strong>
      <p className="muted small">إذا المنتج ما إله ألوان أو مقاسات، اترك اللون والمقاس فاضيين واكتب الكمية بس.</p>
      {rows.map((row, i) => (
        <div className="variant-row" key={i}>
          <VariantFields row={row} onChange={(r) => setRows(rows.map((x, j) => (j === i ? r : x)))} />
          {rows.length > 1 && (
            <button type="button" className="link danger" aria-label={`إزالة نسخة ${i + 1}`}
              onClick={() => setRows(rows.filter((_, j) => j !== i))}>✕</button>
          )}
        </div>
      ))}
      <button type="button" className="link" onClick={() => setRows([...rows, emptyRow()])}>+ نسخة كمان</button>

      {error && <p className="msg error" role="alert">{error}</p>}
      <div className="row">
        <button type="submit" disabled={busy}>{busy ? '...' : 'حفظ المنتج'}</button>
        <button type="button" className="link" onClick={onCancel}>إلغاء</button>
      </div>
    </form>
  )
}

/** Inline form to add one color/size to an existing product. */
export function AddVariantForm({ product, onAdded, onCancel }: {
  product: Product; onAdded: (p: Product) => void; onCancel: () => void
}) {
  const [row, setRow] = useState<VariantRow>(emptyRow())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    try {
      const input = toVariantInput(row)
      if (!input.color && !input.size) throw new Error('اكتب لون أو مقاس للنسخة الجديدة')
      setBusy(true)
      onAdded(await addVariant(product.id, input))
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="variant-row inline-form" onSubmit={submit}>
      <span className="muted small">نسخة جديدة لـ {product.name}:</span>
      <VariantFields row={row} onChange={setRow} />
      <button type="submit" disabled={busy}>{busy ? '...' : 'إضافة'}</button>
      <button type="button" className="link" onClick={onCancel}>إلغاء</button>
      {error && <span className="error-text small" role="alert">{error}</span>}
    </form>
  )
}
