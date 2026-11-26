import { useCallback, useEffect, useState } from 'react'
import { listWaiting, notifyWaiting, type WaitingVariant } from '../lib/api'

const label = (w: WaitingVariant) =>
  !w.variant_name || w.variant_name === 'الافتراضي' ? w.product_name : `${w.product_name} (${w.variant_name})`

/** Customers who asked the bot to tell them when a sold-out item is back (task 3.12). */
export default function WaitingCustomers({ reloadKey }: { reloadKey: number }) {
  const [items, setItems] = useState<WaitingVariant[] | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [message, setMessage] = useState<{ kind: 'info' | 'error'; text: string } | null>(null)

  const load = useCallback(async () => {
    try {
      setItems(await listWaiting())
    } catch (e) {
      setMessage({ kind: 'error', text: (e as Error).message })
    }
  }, [])

  useEffect(() => { load() }, [load, reloadKey])

  async function notify(w: WaitingVariant) {
    if (!window.confirm(`نبعت لـ ${w.waiting} عميل إنه ${label(w)} رجع متوفر؟`)) return
    setBusy(w.variant_id)
    setMessage(null)
    try {
      const r = await notifyWaiting(w.variant_id)
      setMessage({
        kind: r.failed ? 'error' : 'info',
        text: r.failed
          ? `انبعت لـ ${r.sent}، وما انبعت لـ ${r.failed} لأنه قناتهم مش موصولة. بضلّوا بالقائمة.`
          : `انبعت لـ ${r.sent} عميل ✓`,
      })
      await load()
    } catch (e) {
      setMessage({ kind: 'error', text: (e as Error).message })
    } finally {
      setBusy(null)
    }
  }

  if (!items || items.length === 0) return null
  return (
    <div className="card wide alert-panel">
      <h2>عملاء مستنيين منتجات ({items.reduce((n, w) => n + w.waiting, 0)})</h2>
      <p className="muted small">
        لما منتج يخلص، البوت بيسأل العميل إذا بدّه نخبره لما يرجع. بعد ما تزيد الكمية، اكبس "بلّغهم" وبتنبعتلهم رسالة.
      </p>
      {message && <p className={`msg ${message.kind}`} role={message.kind === 'error' ? 'alert' : undefined}>{message.text}</p>}
      <ul className="plain-list">
        {items.map((w) => (
          <li key={w.variant_id}>
            <strong>{label(w)}</strong>
            <span className="badge muted-badge">{w.waiting} مستني</span>
            <span className="muted small">{w.customer_names.slice(0, 3).join('، ')}{w.customer_names.length > 3 ? ' ...' : ''}</span>
            <span className="spacer" />
            {w.stock_quantity > 0 ? (
              <button type="button" className="small" disabled={busy === w.variant_id} onClick={() => notify(w)}>
                {busy === w.variant_id ? '...' : `رجع (${w.stock_quantity}) · بلّغهم`}
              </button>
            ) : (
              <span className="muted small">لسا خلصان</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}
