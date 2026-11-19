import { useEffect, useState, type FormEvent } from 'react'
import EditableCell, { validatePrice } from '../components/EditableCell'
import {
  createOffer, deleteOffer, listOffers, listProducts, updateOffer,
  type Offer, type OfferKind, type OfferStatus, type Product,
} from '../lib/api'
import { useStore } from '../store/StoreProvider'

const KIND_LABEL: Record<OfferKind, string> = {
  percent_off: 'خصم بالنسبة',
  fixed_price: 'سعر ثابت',
  free_delivery: 'توصيل مجاني',
}

const STATUS: Record<OfferStatus, { label: string; cls: string }> = {
  live: { label: 'فعّال', cls: '' },
  scheduled: { label: 'مجدول', cls: 'warn' },
  ended: { label: 'منتهي', cls: 'muted-badge' },
  paused: { label: 'موقوف', cls: 'muted-badge' },
}

// Latin digits, Arabic month names.
const dateFmt = new Intl.DateTimeFormat('ar-u-nu-latn', { dateStyle: 'medium', timeStyle: 'short' })
const fmt = (iso: string | null) => (iso ? dateFmt.format(new Date(iso)) : null)

/** <input type="datetime-local"> value (browser local time) -> ISO with timezone, or null. */
const toIso = (local: string) => (local ? new Date(local).toISOString() : null)

function validatePercent(text: string): string | null {
  const n = Number(text)
  return /^\d{1,2}(\.\d{1,2})?$/.test(text) && n > 0 && n < 100 ? null : 'نسبة بين 0 و 100 (مثل 20)'
}

function target(o: Offer) {
  if (o.kind === 'free_delivery') return 'كل الطلب'
  return o.variant_name ? `${o.product_name} - ${o.variant_name}` : `${o.product_name} (كل النسخ)`
}

function period(o: Offer) {
  const from = fmt(o.starts_at)
  const to = fmt(o.ends_at)
  if (!from && !to) return <span className="muted">بدون مدة</span>
  return <span className="small">{from ? `من ${from}` : 'من هلأ'}{to ? ` لـ ${to}` : ''}</span>
}

function NewOfferForm({ products, onCreated, onCancel }: {
  products: Product[]; onCreated: (o: Offer) => void; onCancel: () => void
}) {
  const { store } = useStore()
  const [name, setName] = useState('')
  const [kind, setKind] = useState<OfferKind>('percent_off')
  const [productId, setProductId] = useState('')
  const [variantId, setVariantId] = useState('')
  const [value, setValue] = useState('')
  const [startsAt, setStartsAt] = useState('')
  const [endsAt, setEndsAt] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const product = products.find((p) => p.id === productId)
  const needsTarget = kind !== 'free_delivery'

  async function submit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    const v = value.trim()
    const problem =
      !name.trim() ? 'اسم العرض مطلوب'
        : needsTarget && !productId ? 'اختار المنتج'
          : kind === 'percent_off' ? validatePercent(v)
            : validatePrice(v)
    if (problem) return setError(problem)
    if (startsAt && endsAt && new Date(endsAt) <= new Date(startsAt)) return setError('تاريخ النهاية لازم يكون بعد البداية')

    setBusy(true)
    try {
      onCreated(await createOffer({
        name: name.trim(),
        kind,
        product_id: needsTarget && !variantId ? productId : null,
        variant_id: needsTarget && variantId ? variantId : null,
        percent: kind === 'percent_off' ? v : null,
        fixed_price: kind === 'fixed_price' ? v : null,
        min_order_total: kind === 'free_delivery' ? v : null,
        starts_at: toIso(startsAt),
        ends_at: toIso(endsAt),
      }))
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const valueLabel =
    kind === 'percent_off' ? 'نسبة الخصم %' : kind === 'fixed_price' ? `سعر العرض (${store?.currency})` : `الحد الأدنى للطلب (${store?.currency})`

  return (
    <form className="card wide" onSubmit={submit}>
      <h2>عرض جديد</h2>
      <div className="form-grid">
        <label>اسم العرض<input required maxLength={100} placeholder="مثلاً: خصم الصيف" value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label>
          نوع العرض
          <select value={kind} onChange={(e) => { setKind(e.target.value as OfferKind); setValue('') }}>
            {Object.entries(KIND_LABEL).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
          </select>
        </label>
        {needsTarget && (
          <>
            <label>
              المنتج
              <select value={productId} onChange={(e) => { setProductId(e.target.value); setVariantId('') }}>
                <option value="">— اختار —</option>
                {products.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </label>
            {product && product.variants.length > 1 && (
              <label>
                النسخة
                <select value={variantId} onChange={(e) => setVariantId(e.target.value)}>
                  <option value="">كل النسخ</option>
                  {product.variants.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
                </select>
              </label>
            )}
          </>
        )}
        <label>{valueLabel}<input required dir="ltr" inputMode="decimal" value={value} onChange={(e) => setValue(e.target.value)} /></label>
        <label>يبلّش <span className="hint">(اختياري)</span><input type="datetime-local" value={startsAt} onChange={(e) => setStartsAt(e.target.value)} /></label>
        <label>بينتهي <span className="hint">(اختياري)</span><input type="datetime-local" value={endsAt} onChange={(e) => setEndsAt(e.target.value)} /></label>
      </div>
      {kind === 'fixed_price' && product && (
        <p className="muted small">سعر {product.name} الأساسي: {product.price} {store?.currency}. إذا سعر العرض أعلى، ما بينطبق.</p>
      )}
      <p className="muted small">إذا في أكتر من عرض على نفس المنتج، بينطبق الأرخص للعميل (عرض واحد بس).</p>
      {error && <p className="msg error" role="alert">{error}</p>}
      <div className="row">
        <button type="submit" disabled={busy}>{busy ? '...' : 'حفظ العرض'}</button>
        <button type="button" className="link" onClick={onCancel}>إلغاء</button>
      </div>
    </form>
  )
}

export default function OffersPage() {
  const { store } = useStore()
  const [offers, setOffers] = useState<Offer[] | null>(null)
  const [products, setProducts] = useState<Product[]>([])
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [showNew, setShowNew] = useState(false)

  useEffect(() => {
    Promise.all([listOffers(), listProducts()])
      .then(([o, p]) => { setOffers(o); setProducts(p) })
      .catch((e) => setError((e as Error).message))
  }, [])

  const replace = (updated: Offer) => setOffers((list) => list?.map((o) => (o.id === updated.id ? updated : o)) ?? null)

  async function toggle(o: Offer) {
    setActionError(null)
    try {
      replace(await updateOffer(o.id, { is_active: !o.is_active }))
    } catch (e) {
      setActionError((e as Error).message)
    }
  }

  async function remove(o: Offer) {
    if (!window.confirm(`حذف العرض "${o.name}"؟`)) return
    setActionError(null)
    try {
      await deleteOffer(o.id)
      setOffers((list) => list?.filter((x) => x.id !== o.id) ?? null)
    } catch (e) {
      setActionError((e as Error).message)
    }
  }

  if (error) return <p className="msg error" role="alert">{error}</p>
  if (!offers) return <p className="muted">جاري التحميل...</p>
  const currency = store?.currency ?? ''

  return (
    <section className="stack wide">
      <div className="row">
        <h1>العروض</h1>
        <span className="spacer" />
        {!showNew && <button type="button" onClick={() => setShowNew(true)}>+ عرض جديد</button>}
      </div>

      {showNew && (
        <NewOfferForm
          products={products}
          onCancel={() => setShowNew(false)}
          onCreated={(o) => { setOffers((list) => [o, ...(list ?? [])]); setShowNew(false) }}
        />
      )}
      {actionError && <p className="msg error" role="alert">{actionError}</p>}

      {offers.length === 0 ? (
        !showNew && <p className="muted">ما في عروض لسّا.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>العرض</th><th>النوع</th><th>على</th><th>القيمة</th><th>المدة</th><th>الحالة</th><th /></tr>
            </thead>
            <tbody>
              {offers.map((o) => (
                <tr key={o.id}>
                  <td><strong>{o.name}</strong></td>
                  <td>{KIND_LABEL[o.kind]}</td>
                  <td>{target(o)}</td>
                  <td>
                    {o.kind === 'percent_off' && (
                      <EditableCell label={`نسبة ${o.name}`} value={o.percent ?? ''} width="4.5rem" validate={validatePercent}
                        onSave={async (t) => replace(await updateOffer(o.id, { percent: t }))} />
                    )}
                    {o.kind === 'fixed_price' && (
                      <EditableCell label={`سعر ${o.name}`} value={o.fixed_price ?? ''} validate={(t) => validatePrice(t)}
                        onSave={async (t) => replace(await updateOffer(o.id, { fixed_price: t }))} />
                    )}
                    {o.kind === 'free_delivery' && (
                      <span className="editable">
                        <span className="muted small">فوق</span>
                        <EditableCell label={`حد ${o.name}`} value={o.min_order_total ?? ''} validate={(t) => validatePrice(t)}
                          onSave={async (t) => replace(await updateOffer(o.id, { min_order_total: t }))} />
                      </span>
                    )}
                    <span className="muted small"> {o.kind === 'percent_off' ? '%' : currency}</span>
                  </td>
                  <td>{period(o)}</td>
                  <td><span className={`badge ${STATUS[o.status].cls}`}>{STATUS[o.status].label}</span></td>
                  <td>
                    <div className="row-actions">
                      <button type="button" className="link small" onClick={() => toggle(o)}>{o.is_active ? 'إيقاف' : 'تشغيل'}</button>
                      <button type="button" className="link small danger" onClick={() => remove(o)}>حذف</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
