import { useCallback, useEffect, useState } from 'react'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import BarList from '../components/BarList'
import { getAdminOverview, type AdminOverview } from '../lib/api'

const num = new Intl.NumberFormat('ar-u-nu-latn', { maximumFractionDigits: 2 })
const pct = new Intl.NumberFormat('ar-u-nu-latn', { style: 'percent', maximumFractionDigits: 0 })
const weekFmt = new Intl.DateTimeFormat('ar-u-nu-latn', { day: 'numeric', month: 'numeric' })
const monthFmt = new Intl.DateTimeFormat('ar-u-nu-latn', { month: 'short', year: '2-digit' })
const CHANNELS: Record<string, string> = { whatsapp: 'واتساب', telegram: 'تيليجرام', web: 'محاكي الويب' }

function money(amount: string | number, currency: string) {
  return `${num.format(Number(amount))} ${currency}`
}

/** The chart colour from the design tokens, following the light/dark theme. */
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

/** Platform numbers for promotion (task 5.3). Demo stores and synthetic orders are off by default. */
export default function AdminOverviewPage() {
  const [includeDemo, setIncludeDemo] = useState(false)
  const [period, setPeriod] = useState<'week' | 'month'>('week')
  const [data, setData] = useState<AdminOverview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const color = useChartColor()

  const load = useCallback(async () => {
    try {
      setData(await getAdminOverview(includeDemo, period))
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [includeDemo, period])

  useEffect(() => { load() }, [load])

  // The growth chart shows revenue in the platform's main currency (the one with most orders).
  const mainCurrency = data?.sales[0]?.currency ?? null
  const points = (data?.growth ?? []).map((g) => ({
    ...g,
    label: (period === 'week' ? weekFmt : monthFmt).format(new Date(g.start)),
    revenueMain: mainCurrency ? Number(g.revenue[mainCurrency] ?? 0) : 0,
  }))

  return (
    <section className="stack wide">
      <div className="page-head">
        <h1>نظرة عامة على المنصة</h1>
        <label className="inline-check">
          <input type="checkbox" checked={includeDemo} onChange={(e) => setIncludeDemo(e.target.checked)} />
          مع البيانات التجريبية
        </label>
      </div>
      <p className="muted small">
        {includeDemo
          ? 'الأرقام فيها المتاجر التجريبية والطلبات المصطنعة. ما تستخدميها للترويج.'
          : 'أرقام حقيقية بس: بدون المتاجر التجريبية والطلبات المصطنعة، وبدون الطلبات الملغية والمرتجعة.'}
      </p>

      {error && <p className="msg error" role="alert">{error}</p>}
      {!data && !error && <p className="muted">جاري التحميل...</p>}

      {data && (
        <>
          <div className="stats">
            <div className="stat"><span className="value">{num.format(data.stores_total)}</span><span className="label">متجر</span></div>
            <div className="stat"><span className="value">{num.format(data.stores_active_30d)}</span><span className="label">متجر فعّال آخر 30 يوم</span></div>
            <div className="stat"><span className="value">{num.format(data.stores_new_this_month)}</span><span className="label">متجر جديد هالشهر</span></div>
            <div className="stat"><span className="value">{num.format(data.orders_total)}</span><span className="label">طلب</span></div>
            <div className="stat">
              <span className="value">{num.format(data.orders_by_bot)}</span>
              <span className="label">طلب سجّله البوت لحاله</span>
            </div>
            <div className="stat">
              <span className="value">{data.bot_share === null ? '—' : pct.format(data.bot_share)}</span>
              <span className="label">من المحادثات خلّصها البوت بدون تحويل</span>
            </div>
          </div>

          <div className="card wide">
            <h2>المبيعات</h2>
            {data.sales.length === 0 ? (
              <p className="muted small">ما في مبيعات لسّا.</p>
            ) : (
              <div className="stats">
                {data.sales.map((s) => (
                  <div className="stat" key={s.currency}>
                    <span className="value">{money(s.revenue, s.currency)}</span>
                    <span className="label">{num.format(s.orders)} طلب بعملة {s.currency}</span>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="card wide chart-panel">
            <div className="page-head">
              <h2>النمو {mainCurrency ? `(المبيعات بـ ${mainCurrency})` : ''}</h2>
              <div className="tabs" role="group" aria-label="الفترة">
                <button type="button" className={period === 'week' ? 'active' : ''} onClick={() => setPeriod('week')}>أسبوعي</button>
                <button type="button" className={period === 'month' ? 'active' : ''} onClick={() => setPeriod('month')}>شهري</button>
              </div>
            </div>
            <div className="chart-box" role="img"
              aria-label={`عدد الطلبات ${period === 'week' ? 'بآخر 12 أسبوع' : 'بآخر 12 شهر'}`}>
              <ResponsiveContainer>
                <LineChart data={points} margin={{ top: 8, right: 4, left: 4, bottom: 0 }}>
                  <CartesianGrid vertical={false} stroke="var(--rule)" />
                  <XAxis dataKey="label" reversed tickLine={false} axisLine={{ stroke: 'var(--rule)' }}
                    tick={{ fill: 'var(--muted)', fontSize: 11 }} minTickGap={12} />
                  <YAxis orientation="right" allowDecimals={false} tickLine={false} axisLine={false} width={40}
                    tick={{ fill: 'var(--muted)', fontSize: 11 }} />
                  <Tooltip content={({ active, payload }) => {
                    if (!active || !payload?.length) return null
                    const p = payload[0].payload as (typeof points)[number]
                    return (
                      <div className="chart-tip">
                        <strong>{period === 'week' ? `أسبوع ${p.label}` : p.label}</strong>
                        {num.format(p.orders)} طلب، {num.format(p.new_stores)} متجر جديد
                        {mainCurrency && <>، {money(p.revenueMain, mainCurrency)}</>}
                      </div>
                    )
                  }} />
                  <Line type="monotone" dataKey="orders" stroke={color} strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
            <details className="table-view">
              <summary>عرض كجدول</summary>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>الفترة</th><th>الطلبات</th><th>المبيعات</th><th>متاجر جديدة</th></tr></thead>
                  <tbody>
                    {[...points].reverse().map((p) => (
                      <tr key={p.start}>
                        <td>{p.label}</td>
                        <td>{num.format(p.orders)}</td>
                        <td>{Object.entries(p.revenue).map(([cur, v]) => money(v, cur)).join('، ') || '0'}</td>
                        <td>{num.format(p.new_stores)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          </div>

          <div className="charts-row">
            <div className="card wide">
              <h2>أنشط المتاجر</h2>
              {data.top_stores.length === 0
                ? <p className="muted small">ما في طلبات لسّا.</p>
                : <BarList ariaLabel="أنشط المتاجر بعدد الطلبات"
                    items={data.top_stores.map((s) => ({ key: s.id, label: s.name, value: s.orders, valueLabel: `${num.format(s.orders)} طلب` }))} />}
            </div>
            <div className="card wide">
              <h2>حسب القناة</h2>
              {data.channels.length === 0 ? <p className="muted small">ما في محادثات لسّا.</p> : (
                <div className="table-wrap">
                  <table>
                    <thead><tr><th>القناة</th><th>المحادثات</th><th>الطلبات</th></tr></thead>
                    <tbody>
                      {data.channels.map((c) => (
                        <tr key={c.channel}>
                          <td>{CHANNELS[c.channel] ?? c.channel}</td>
                          <td>{num.format(c.conversations)}</td>
                          <td>{num.format(c.orders)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </section>
  )
}
