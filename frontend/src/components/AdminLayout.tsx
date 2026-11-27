import { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { signOut, useAuth } from '../auth/AuthProvider'

/** Frame for the platform admin pages (tasks 5.2-5.4). Same look as the store dashboard. */
export default function AdminLayout() {
  const { session } = useAuth()
  const [menuOpen, setMenuOpen] = useState(false)
  const location = useLocation()

  useEffect(() => setMenuOpen(false), [location.pathname])

  return (
    <div className={`app ${menuOpen ? 'menu-open' : ''}`}>
      <header className="mobilebar">
        <button type="button" onClick={() => setMenuOpen(true)} aria-expanded={menuOpen} aria-controls="sidebar">القائمة</button>
        <strong>إدارة المنصة</strong>
      </header>

      <aside className="sidebar" id="sidebar">
        <div className="brand">
          إدارة المنصة
          <small>كل المتاجر</small>
        </div>

        <nav className="nav-group" aria-label="المنصة">
          <span>المنصة</span>
          <NavLink to="/admin" end>نظرة عامة</NavLink>
          <NavLink to="/admin/stores">المتاجر</NavLink>
          <NavLink to="/admin/whatsapp">طلبات ربط واتساب</NavLink>
        </nav>

        <div className="sidebar-foot">
          <span dir="ltr">{session?.user.email}</span>
          <button type="button" className="link" onClick={signOut}>تسجيل الخروج</button>
        </div>
      </aside>
      <div className="scrim" onClick={() => setMenuOpen(false)} />

      <main className="shell">
        <Outlet />
      </main>
    </div>
  )
}
