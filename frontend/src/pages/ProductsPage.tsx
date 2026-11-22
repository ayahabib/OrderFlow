import { Fragment, useEffect, useMemo, useState } from 'react'
import ImportPanel from '../components/ImportPanel'
import EditableCell, { validatePrice, validateQuantity } from '../components/EditableCell'
import { AddVariantForm, NewProductForm } from '../components/ProductForms'
import {
  deleteProduct, deleteVariant, listProducts, updateProductPrice, updateVariant, type Product, type Variant,
} from '../lib/api'
import { useStore } from '../store/StoreProvider'

/** Same light normalization as the backend, so searching "احمر" finds "أحمر". */
function norm(text: string): string {
  return text.replace(/[أإآٱ]/g, 'ا').replace(/ة/g, 'ه').replace(/ى/g, 'ي')
    .replace(/[ً-ٰٟـ]/g, '').toLowerCase().trim()
}

function stockBadge(v: Variant) {
  if (v.stock_quantity === 0) return <span className="badge danger">نفد</span>
  if (v.stock_quantity <= v.low_stock_threshold) return <span className="badge warn">قليل</span>
  return null
}

const byName = (a: Product, b: Product) => a.name.localeCompare(b.name, 'ar')

export default function ProductsPage() {
  const { store } = useStore()
  const [products, setProducts] = useState<Product[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [showNew, setShowNew] = useState(false)
  const [addingTo, setAddingTo] = useState<string | null>(null)
  const [showImport, setShowImport] = useState(false)

  const reload = () => listProducts().then(setProducts).catch((e) => setError((e as Error).message))
  useEffect(() => {
    reload()
  }, [])

  const visible = useMemo(() => {
    if (!products) return []
    const q = norm(query)
    if (!q) return products
    return products.filter((p) =>
      norm(p.name).includes(q) || p.variants.some((v) => norm(v.name).includes(q)) || norm(p.category ?? '').includes(q),
    )
  }, [products, query])

  function replaceProduct(updated: Product) {
    setProducts((list) => {
      const rest = (list ?? []).filter((p) => p.id !== updated.id)
      return [...rest, updated].sort(byName)
    })
  }

  function replaceVariant(productId: string, updated: Variant) {
    setProducts((list) => list?.map((p) => p.id !== productId ? p : {
      ...p, variants: p.variants.map((v) => (v.id === updated.id ? updated : v)),
    }) ?? null)
  }

  async function removeProduct(p: Product) {
    const msg = `حذف "${p.name}" مع كل نسخه (${p.variants.length})؟\nما بتقدر ترجعه. الطلبات القديمة ما بتتأثر.`
    if (!window.confirm(msg)) return
    setActionError(null)
    try {
      await deleteProduct(p.id)
      setProducts((list) => list?.filter((x) => x.id !== p.id) ?? null)
    } catch (e) {
      setActionError((e as Error).message)
    }
  }

  async function removeVariant(p: Product, v: Variant) {
    if (!window.confirm(`حذف النسخة "${v.name}" من "${p.name}"؟`)) return
    setActionError(null)
    try {
      await deleteVariant(v.id)
      setProducts((list) => list?.map((x) => x.id !== p.id ? x : {
        ...x, variants: x.variants.filter((y) => y.id !== v.id),
      }) ?? null)
    } catch (e) {
      setActionError((e as Error).message)
    }
  }

  if (error) return <p className="msg error" role="alert">{error}</p>
  if (!products) return <p className="muted">جاري التحميل...</p>

  const currency = store?.currency ?? ''
  const totalVariants = products.reduce((n, p) => n + p.variants.length, 0)
  const newForm = showNew && (
    <NewProductForm
      onCancel={() => setShowNew(false)}
      onCreated={(p) => { replaceProduct(p); setShowNew(false) }}
    />
  )

  return (
    <section className="stack wide">
      <div className="row">
        <h1>المنتجات</h1>
        {products.length > 0 && <span className="muted">{products.length} منتج، {totalVariants} نسخة</span>}
        <span className="spacer" />
        {products.length > 0 && (
          <input type="search" placeholder="بحث..." aria-label="بحث بالمنتجات"
            value={query} onChange={(e) => setQuery(e.target.value)} />
        )}
        {!showImport && <button type="button" className="link" onClick={() => setShowImport(true)}>رفع من Excel</button>}
        {!showNew && <button type="button" onClick={() => setShowNew(true)}>+ منتج جديد</button>}
      </div>

      {showImport && <ImportPanel onImported={reload} onClose={() => setShowImport(false)} />}
      {newForm}
      {actionError && <p className="msg error" role="alert">{actionError}</p>}

      {products.length === 0 ? (
        !showNew && <p className="muted">ما في منتجات لسّا. أضف منتج، أو <button type="button" className="link" onClick={() => setShowImport(true)}>ارفع ملف المخزون</button>.</p>
      ) : (
        <>
          <p className="muted small">عدّل الكمية أو السعر واضغط Enter. السعر الخاص فاضي = سعر المنتج الأساسي.</p>
          <div className="table-wrap">
            <table className="products">
              <thead>
                <tr>
                  <th>المنتج</th>
                  <th>السعر الأساسي ({currency})</th>
                  <th>النسخة</th>
                  <th>الكمية</th>
                  <th>سعر خاص ({currency})</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((p) => (
                  <Fragment key={p.id}>
                    {p.variants.map((v, i) => (
                      <tr key={v.id} className={i === 0 ? 'group-start' : undefined}>
                        {i === 0 && (
                          <>
                            <td rowSpan={p.variants.length}>
                              <strong>{p.name}</strong>
                              {p.category && <div className="muted small">{p.category}</div>}
                              <div className="row-actions">
                                <button type="button" className="link small" onClick={() => setAddingTo(p.id)}>+ نسخة</button>
                                <button type="button" className="link small danger" onClick={() => removeProduct(p)}>حذف</button>
                              </div>
                            </td>
                            <td rowSpan={p.variants.length}>
                              <EditableCell
                                label={`السعر الأساسي لـ ${p.name}`}
                                value={p.price}
                                validate={(t) => validatePrice(t)}
                                onSave={async (t) => replaceProduct(await updateProductPrice(p.id, t))}
                              />
                            </td>
                          </>
                        )}
                        <td>
                          {v.name} {stockBadge(v)}
                          {p.variants.length > 1 && (
                            <button type="button" className="link small danger" aria-label={`حذف النسخة ${v.name}`}
                              onClick={() => removeVariant(p, v)}>✕</button>
                          )}
                        </td>
                        <td>
                          <EditableCell
                            label={`كمية ${p.name} ${v.name}`}
                            value={String(v.stock_quantity)}
                            width="5rem"
                            validate={validateQuantity}
                            onSave={async (t) => replaceVariant(p.id, await updateVariant(v.id, { stock_quantity: Number(t) }))}
                          />
                        </td>
                        <td>
                          <EditableCell
                            label={`سعر خاص لـ ${p.name} ${v.name}`}
                            value={v.price_override ?? ''}
                            placeholder={p.price}
                            validate={(t) => validatePrice(t, true)}
                            onSave={async (t) => replaceVariant(p.id, await updateVariant(v.id, { price_override: t === '' ? null : t }))}
                          />
                          {v.offer_price && (
                            <div className="offer-price small" title={v.offer_name ?? undefined}>
                              عرض: <strong>{v.offer_price}</strong> <s className="muted">{v.effective_price}</s> · {v.offer_name}
                            </div>
                          )}
                        </td>
                      </tr>
                    ))}
                    {addingTo === p.id && (
                      <tr>
                        <td colSpan={5}>
                          <AddVariantForm
                            product={p}
                            onCancel={() => setAddingTo(null)}
                            onAdded={(updated) => { replaceProduct(updated); setAddingTo(null) }}
                          />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
          {visible.length === 0 && <p className="muted">ما في نتائج لـ "{query}"</p>}
        </>
      )}
    </section>
  )
}
