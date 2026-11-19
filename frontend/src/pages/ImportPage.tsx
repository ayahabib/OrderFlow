import { useRef, useState, type FormEvent } from 'react'
import { confirmImport, previewImport, type ImportPreview, type ImportResult } from '../lib/api'
import { useStore } from '../store/StoreProvider'

const MAX_BYTES = 2 * 1024 * 1024

/** Upload an Excel/CSV file, review what will change, then confirm. */
export default function ImportPage() {
  const { store } = useStore()
  const inputRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<ImportPreview | null>(null)
  const [result, setResult] = useState<ImportResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function choose(f: File | null) {
    setFile(f)
    setPreview(null)
    setResult(null)
    setError(f && f.size > MAX_BYTES ? 'الملف أكبر من 2 ميغابايت' : null)
  }

  async function runPreview(e: FormEvent) {
    e.preventDefault()
    if (!file) return
    setBusy(true)
    setError(null)
    try {
      setPreview(await previewImport(file))
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function runConfirm() {
    if (!file) return
    setBusy(true)
    setError(null)
    try {
      setResult(await confirmImport(file))
      setPreview(null)
      setFile(null)
      if (inputRef.current) inputRef.current.value = ''
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="stack wide">
      <h1>رفع المخزون</h1>

      <form className="card wide" onSubmit={runPreview}>
        <p className="muted">
          ملف Excel (.xlsx) أو CSV. الأعمدة المطلوبة: <code>product_name</code>، <code>price</code>،{' '}
          <code>quantity</code>. اختياري: <code>color</code>، <code>size</code>، <code>variant_price</code>،{' '}
          <code>sku</code>، <code>category</code>، <code>description</code>، <code>aliases</code> (مفصولة بـ |).
          الصفوف الي إلها نفس اسم المنتج بتصير ألوان/مقاسات لنفس المنتج.
        </p>
        <input ref={inputRef} type="file" accept=".xlsx,.csv"
          onChange={(e) => choose(e.target.files?.[0] ?? null)} />
        <button type="submit" disabled={!file || busy || (file?.size ?? 0) > MAX_BYTES}>
          {busy && !preview ? '...' : 'معاينة'}
        </button>
      </form>

      {error && <p className="msg error" role="alert">{error}</p>}

      {result && (
        <div className="msg info">
          تم الرفع ✓ منتجات جديدة: {result.products_created}، محدّثة: {result.products_updated}. نسخ جديدة:{' '}
          {result.variants_created}، محدّثة: {result.variants_updated}.
          {result.skipped_rows.length > 0 && ` تم تجاهل ${result.skipped_rows.length} صف فيهم أخطاء.`}
        </div>
      )}

      {preview && (
        <>
          <p>
            {preview.total_rows} صف بالملف: <strong>{preview.valid_rows} صالح</strong>
            {preview.errors.length > 0 && <>، <strong className="error-text">{preview.errors.length} فيه أخطاء</strong></>}
          </p>

          {preview.errors.length > 0 && (
            <div className="msg error">
              <strong>الصفوف هاي ما رح تنرفع:</strong>
              <ul>
                {preview.errors.map((e) => (
                  <li key={e.row}>صف {e.row}: {e.message}</li>
                ))}
              </ul>
            </div>
          )}

          {preview.products.length > 0 && (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>المنتج</th>
                    <th>النسخة</th>
                    <th>الكمية</th>
                    <th>السعر</th>
                    <th>صف</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.products.flatMap((p) =>
                    p.variants.map((v, i) => (
                      <tr key={v.row}>
                        <td>{i === 0 && <>{p.name} {p.is_new && <span className="badge">جديد</span>}</>}</td>
                        <td>{v.name}</td>
                        <td>
                          {v.current_quantity === null || v.current_quantity === v.quantity
                            ? v.quantity
                            : <>{v.current_quantity} ← <strong>{v.quantity}</strong></>}
                          {v.current_quantity === null && !p.is_new && <span className="badge">نسخة جديدة</span>}
                        </td>
                        <td>{v.price_override ?? p.price} {store?.currency}</td>
                        <td className="muted">{v.row}</td>
                      </tr>
                    )),
                  )}
                </tbody>
              </table>
            </div>
          )}

          <button type="button" onClick={runConfirm} disabled={busy || preview.valid_rows === 0}>
            {busy ? '...' : `تأكيد ورفع ${preview.valid_rows} صف`}
          </button>
        </>
      )}
    </section>
  )
}
