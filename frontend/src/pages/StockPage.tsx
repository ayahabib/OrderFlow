import { useCallback, useEffect, useState } from 'react'
import BarList from '../components/BarList'
import EditableCell, { validateQuantity } from '../components/EditableCell'
import StockChat from '../components/StockChat'
import WaitingCustomers from '../components/WaitingCustomers'
import { getLowStock, getStockByProduct, updateVariant, type LowStockItem, type ProductStock } from '../lib/api'

const REFRESH_MS = 60_000

const label = (i: LowStockItem) => (i.variant_name === 'الافتراضي' ? i.product_name : `${i.product_name} (${i.variant_name})`)

/** Stock at a glance (task 3.2): what's sold out or running low first, then stock per product. */
export default function StockPage() {
  const [low, setLow] = useState<LowStockItem[] | null>(null)
  const [stock, setStock] = useState<ProductStock[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)   // stock changed: re-read who is waiting

  const load = useCallback(async () => {
    try {
      const [l, s] = await Promise.all([getLowStock(), getStockByProduct()])
      setLow(l)
      setStock(s)
      setReloadKey((k) => k + 1)
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])

  useEffect(() => {
    load()
    const timer = window.setInterval(load, REFRESH_MS)
    return () => window.clearInterval(timer)
  }, [load])

  const soldOut = low?.filter((i) => i.stock_quantity === 0) ?? []
  const runningLow = low?.filter((i) => i.stock_quantity > 0) ?? []

  const save = async (id: string, changes: { stock_quantity?: number; low_stock_threshold?: number }) => {
    await updateVariant(id, changes)
    await load()   // the item may leave the list once it's above its alert level
  }

  const rows = (items: LowStockItem[]) => items.map((i) => (
    <tr key={i.variant_id}>
      <td>{label(i)}</td>
      <td>
        <EditableCell label={`كمية ${label(i)}`} value={String(i.stock_quantity)} width="5rem"
          validate={validateQuantity} onSave={(t) => save(i.variant_id, { stock_quantity: Number(t) })} />
      </td>
      <td>
        <EditableCell label={`حد التنبيه لـ ${label(i)}`} value={String(i.low_stock_threshold)} width="4.5rem"
          validate={validateQuantity} onSave={(t) => save(i.variant_id, { low_stock_threshold: Number(t) })} />
      </td>
    </tr>
  ))

  return (
    <section className="stack wide">
      <div className="row">
        <h1>المخزون</h1>
        <span className="spacer" />
        <button type="button" className="link" onClick={load}>تحديث</button>
      </div>
      <p className="muted small">
        المنتج بيبيّن هون لما كميته توصل لحد التنبيه أو أقل (الافتراضي 5). عدّل الكمية أو الحد واضغط Enter.
      </p>
      {error && <p className="msg error">{error}</p>}

      {!low ? <p className="muted">جاري التحميل...</p> : low.length === 0 ? (
        <p className="msg info">كل المنتجات فوق حد التنبيه.</p>
      ) : (
        <div className="stock-alerts">
          {soldOut.length > 0 && (
            <div className="card wide alert-panel sold-out">
              <h2>خلصان ({soldOut.length})</h2>
              <p className="muted small">البوت بيحكي للعميل إنه مش متوفر لحد ما تزيد الكمية.</p>
              <div className="table-wrap">
                <table className="stock-table"><thead><tr><th>المنتج</th><th>الكمية</th><th>حد التنبيه</th></tr></thead><tbody>{rows(soldOut)}</tbody></table>
              </div>
            </div>
          )}
          {runningLow.length > 0 && (
            <div className="card wide alert-panel">
              <h2>قرّب يخلص ({runningLow.length})</h2>
              <div className="table-wrap">
                <table className="stock-table"><thead><tr><th>المنتج</th><th>الكمية</th><th>حد التنبيه</th></tr></thead><tbody>{rows(runningLow)}</tbody></table>
              </div>
            </div>
          )}
        </div>
      )}

      <WaitingCustomers reloadKey={reloadKey} />

      <StockChat onChanged={load} />

      <div className="card wide chart-panel">
        <h2>المخزون حسب المنتج (قطعة)</h2>
        {!stock ? null : stock.length === 0 ? <p className="muted small">ما في منتجات لسّا.</p> : (
          <BarList ariaLabel="مجموع القطع لكل منتج، الأقل أولاً" items={stock.map((p) => ({
            key: p.product_id, label: p.name, value: p.total_stock,
            valueLabel: `${p.total_stock} قطعة`, title: `${p.variants_count} نسخة (لون/مقاس)`,
          }))} />
        )}
      </div>
    </section>
  )
}
