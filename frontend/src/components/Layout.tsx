import { useCallback, useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { signOut, useAuth } from '../auth/AuthProvider'
import { getLowStock, listConversations, listOrders } from '../lib/api'
import { useStore } from '../store/StoreProvider'
import AssistantDrawer from './AssistantDrawer'
import { BotOffBanner, BotSwitch } from './BotSwitch'

const COUNTS_MS = 30_000

/** What is waiting for the owner right now: escalated chats and new orders. */
function useWaitingCounts() {
  const [counts, setCounts] = useState({ chats: 0, orders: 0, lowStock: 0 })
  const location = useLocation()

  const load = useCallback(async () => {
    try {
      const [chats, orders, low] = await Promise.all([
        listConversations('waiting', ''), listOrders({ status: 'new' }), getLowStock(),
      ])
      setCounts({ chats: chats.length, orders: orders.length, lowStock: low.length })
    } catch {
      // Counts are a hint; the pages themselves show errors.
    }
  }, [])

  useEffect(() => {
    load()
    const timer = window.setInterval(load, COUNTS_MS)
    return () => window.clearInterval(timer)
  }, [load, location.pathname])  // refresh after moving between pages, e.g. after answering a chat

  return counts
}

function Count({ n, label }: { n: number; label: string }) {
  if (n <= 0) return null
  return <span className="count" aria-label={`${n} ${label}`}>{n}</span>
}

/** Dashboard frame: sidebar grouped by job, with counts of what needs the owner. */
export default function Layout() {
  const { store } = useStore()
  const { session } = useAuth()
  const counts = useWaitingCounts()
  const [menuOpen, setMenuOpen] = useState(false)
  const location = useLocation()

  useEffect(() => setMenuOpen(false), [location.pathname])  // close the phone menu after navigating

  const waiting = counts.chats + counts.orders

  return (
    <div className={`app ${menuOpen ? 'menu-open' : ''}`}>
      <header className="mobilebar">
        <button type="button" onClick={() => setMenuOpen(true)} aria-expanded={menuOpen} aria-controls="sidebar">القائمة</button>
        <strong>{store?.name}</strong>
        <span className="spacer" />
        <Count n={waiting} label="بانتظارك" />
      </header>

      <aside className="sidebar" id="sidebar">
        <div className="brand">
          {store?.name}
          <small>لوحة المتجر</small>
        </div>
        <BotSwitch className="in-sidebar" />

        <nav className="nav-group" aria-label="اليوم">
          <span>اليوم</span>
          <NavLink to="/dashboard">نظرة عامة</NavLink>
          <NavLink to="/conversations">المحادثات <Count n={counts.chats} label="محادثة بانتظار ردك" /></NavLink>
          <NavLink to="/orders">الطلبات <Count n={counts.orders} label="طلب جديد" /></NavLink>
          <NavLink to="/customers">العملاء</NavLink>
        </nav>

        <nav className="nav-group" aria-label="المتجر">
          <span>المتجر</span>
          <NavLink to="/products">المنتجات والأسعار</NavLink>
          <NavLink to="/stock">المخزون <Count n={counts.lowStock} label="منتج قرّب يخلص أو خلصان" /></NavLink>
          <NavLink to="/knowledge">معلومات المتجر</NavLink>
        </nav>

        <nav className="nav-group" aria-label="تجربة">
          <span>تجربة</span>
          <NavLink to="/simulator">محاكي الشات</NavLink>
          <NavLink to="/settings">الإعدادات</NavLink>
          <NavLink to="/activity">سجل النشاط</NavLink>
        </nav>

        <div className="sidebar-foot">
          <span dir="ltr">{session?.user.email}</span>
          <button type="button" className="link" onClick={signOut}>تسجيل الخروج</button>
        </div>
      </aside>
      <div className="scrim" onClick={() => setMenuOpen(false)} />

      <main className="shell">
        <BotOffBanner />
        <Outlet />
      </main>
      <AssistantDrawer />
    </div>
  )
}
