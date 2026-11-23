import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { getForecast, type ForecastReport } from '../lib/api'

const WITHIN = [7, 14, 30]
const REFRESH_MS = 60_000
const dateFmt = new Intl.DateTimeFormat('ar-u-nu-latn', { weekday: 'long', day: 'numeric', month: 'numeric' })
const num = new Intl.NumberFormat('ar-u-nu-latn', { maximumFractionDigits: 1 })

const label = (product: string, variant: string) => (variant === 'الافتراضي' ? product : `${product} (${variant})`)
const when = (days: number) => (days === 0 ? 'اليوم' : days === 1 ? 'بكرا' : `خلال ${days} يوم`)

/** Products expected to run out soon (task 3.4): stock ÷ average daily sales, computed by the backend. */
export default function RunOutForecast() {
  const [within, setWithin] = useState(14)
  const [report, setReport] = useState<ForecastReport | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setReport(await getForecast(within))
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [within])

  useEffect(() => {
    load()
    const timer = window.setInterval(load, REFRESH_MS)
    return () => window.clearInterval(timer)
  }, [load])

  return (
    <div className="card wide">
      <div className="row">
        <h2>متوقع يخلص قريباً</h2>
        <span className="spacer" />
        <div className="tabs" role="tablist" aria-label="خلال كم يوم">
          {WITHIN.map((d) => (
            <button key={d} type="button" role="tab" aria-selected={within === d} className={within === d ? 'active' : ''}
              onClick={() => setWithin(d)}>{d} يوم</button>
          ))}
        </div>
      </div>
      <p className="muted small">
        الحساب: الكمية الحالية ÷ متوسط البيع باليوم بآخر {report?.window_days ?? 28} يوم. المنتج الخلصان أصلاً بتلاقيه
        بـ <Link to="/stock">صفحة المخزون</Link>.
      </p>
      {error && <p className="msg error">{error}</p>}
      {!report ? <p className="muted">جاري التحميل...</p> : report.items.length === 0 ? (
        <p className="msg info">ما في منتج متوقع يخلص خلال {within} يوم بحسب المبيعات الأخيرة.</p>
      ) : (
        <div className="table-wrap">
          <table className="stock-table">
            <thead><tr><th>المنتج</th><th>الكمية</th><th>البيع باليوم</th><th>بيخلص</th></tr></thead>
            <tbody>
              {report.items.map((i) => (
                <tr key={i.variant_id}>
                  <td>{label(i.product_name, i.variant_name)}</td>
                  <td>{i.stock_quantity}</td>
                  <td title={`انباع ${i.sold} بآخر ${report.window_days} يوم`}>{num.format(Number(i.daily_rate))}</td>
                  <td title={dateFmt.format(new Date(`${i.run_out_date}T00:00:00`))}>
                    <strong>{when(i.days_left)}</strong>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
