import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import BarList from '../components/BarList'
import { getSalesReport, type SalesReport } from '../lib/api'

const PERIODS = [7, 30, 90]
const REFRESH_MS = 60_000
const dayFmt = new Intl.DateTimeFormat('ar-u-nu-latn', { day: 'numeric', month: 'numeric' })
const longDayFmt = new Intl.DateTimeFormat('ar-u-nu-latn', { weekday: 'long', day: 'numeric', month: 'long' })
const num = new Intl.NumberFormat('ar-u-nu-latn', { maximumFractionDigits: 2 })

// Dates arrive as "YYYY-MM-DD" (the store's local day): read them as local dates, not UTC.
const asDate = (d: string) => new Date(`${d}T00:00:00`)

/** The chart color from the design tokens (validated per light/dark surface), following the theme. */
function useChartColor() {
  const read = () => getComputedStyle(document.documentElement).getPropertyValue('--chart').trim() || '#257a4a'
  const [color, setColor] = useState(read)
  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const update = () => setColor(read())
    mq.addEventListener('change', update)
    return () => mq.removeEventListener('change', update)
  }, [])
  return color
}

export default function DashboardPage() {
  const [days, setDays] = useState(30)
  const [report, setReport] = useState<SalesReport | null>(null)
  const [error, setError] = useState<string | null>(null)
  const color = useChartColor()

  const load = useCallback(async () => {
    try {
      setReport(await getSalesReport(days))
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [days])

  useEffect(() => {
    load()
    const timer = window.setInterval(load, REFRESH_MS)   // new orders show up without reloading
    window.addEventListener('focus', load)
    return () => {
      window.clearInterval(timer)
      window.removeEventListener('focus', load)
    }
  }, [load])

  const cur = report?.currency ?? ''
  const money = (v: string | number) => `${num.format(Number(v))} ${cur}`
  const chartDays = report?.days.map((d) => ({ ...d, value: Number(d.revenue) })) ?? []
  const top = report?.top_products ?? []

  return (
    <section className="stack wide">
      <div className="row">
        <h1>نظرة عامة</h1>
        <span className="spacer" />
        <div className="tabs" role="tablist" aria-label="الفترة">
          {PERIODS.map((p) => (
            <button key={p} type="button" role="tab" aria-selected={days === p} className={days === p ? 'active' : ''}
              onClick={() => setDays(p)}>آخر {p} يوم</button>
          ))}
        </div>
      </div>

      {error && <p className="msg error">{error}</p>}
      {!report ? <p className="muted">جاري التحميل...</p> : (
        <>
          <div className="stats">
            <div className="stat">
              <span className="value">{money(report.revenue)}</span>
              <span className="label">المبيعات بآخر {days} يوم</span>
            </div>
            <div className="stat">
              <span className="value">{report.orders_count}</span>
              <span className="label">طلب</span>
            </div>
            <div className="stat">
              <span className="value">{report.average_order ? money(report.average_order) : '—'}</span>
              <span className="label">متوسط الطلب</span>
            </div>
            <Link to="/orders" className={`stat ${report.new_orders > 0 ? 'needs-you' : ''}`}>
              <span className="value">{report.new_orders + report.in_progress_orders}</span>
              <span className="label">
                {report.new_orders + report.in_progress_orders === 0
                  ? 'ما في طلبات مفتوحة'
                  : `طلبات مفتوحة: ${report.new_orders} جديد، ${report.in_progress_orders} قيد التنفيذ`}
              </span>
            </Link>
          </div>

          <div className="charts-row">
            <div className="card wide chart-panel">
              <h2>المبيعات اليومية ({cur})</h2>
              {report.orders_count === 0 ? (
                <p className="muted small">ما في مبيعات بهالفترة لسّا. الطلبات المؤكدة من الشات بتظهر هون.</p>
              ) : (
                <div className="chart-box" role="img" aria-label={`المبيعات اليومية بآخر ${days} يوم`}>
                  <ResponsiveContainer>
                    {/* Right-to-left: the newest day sits on the left end, the axis labels on the right. */}
                    <BarChart data={chartDays} margin={{ top: 8, right: 4, left: 4, bottom: 0 }} barCategoryGap={2}>
                      <CartesianGrid vertical={false} stroke="var(--rule)" />
                      <XAxis dataKey="day" reversed tickFormatter={(d) => dayFmt.format(asDate(d))} tickLine={false}
                        axisLine={{ stroke: 'var(--rule)' }} tick={{ fill: 'var(--muted)', fontSize: 11 }} minTickGap={16} />
                      <YAxis orientation="right" tickLine={false} axisLine={false} width={48}
                        tick={{ fill: 'var(--muted)', fontSize: 11 }} tickFormatter={(v) => num.format(v)} />
                      <Tooltip cursor={{ fill: 'var(--olive-tint)' }} content={({ active, payload }) => {
                        if (!active || !payload?.length) return null
                        const d = payload[0].payload as (typeof chartDays)[number]
                        return (
                          <div className="chart-tip">
                            <strong>{longDayFmt.format(asDate(d.day))}</strong>
                            {money(d.revenue)}، {d.orders_count} طلب
                          </div>
                        )
                      }} />
                      <Bar dataKey="value" fill={color} radius={[4, 4, 0, 0]} maxBarSize={28} isAnimationActive={false} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              )}
              <details className="table-view">
                <summary>عرض كجدول</summary>
                <div className="table-wrap">
                  <table>
                    <thead><tr><th>اليوم</th><th>الطلبات</th><th>المبيعات</th></tr></thead>
                    <tbody>
                      {[...report.days].reverse().filter((d) => d.orders_count > 0).map((d) => (
                        <tr key={d.day}><td>{longDayFmt.format(asDate(d.day))}</td><td>{d.orders_count}</td><td>{money(d.revenue)}</td></tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </details>
            </div>

            <div className="card wide chart-panel">
              <h2>الأكثر طلباً (قطعة)</h2>
              {top.length === 0 ? <p className="muted small">بتظهر هون لما يصير في طلبات.</p> : (
                <BarList ariaLabel="المنتجات الأكثر طلباً حسب عدد القطع" items={top.map((p) => ({
                  key: p.product_name, label: p.product_name, value: p.quantity,
                  valueLabel: `${p.quantity} قطعة`, title: money(p.revenue),
                }))} />
              )}
            </div>
          </div>
        </>
      )}
    </section>
  )
}
