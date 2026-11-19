import { NavLink, Outlet } from 'react-router-dom'
import { signOut, useAuth } from '../auth/AuthProvider'
import { useStore } from '../store/StoreProvider'

/** Dashboard frame: top bar with store name, nav, and sign-out. */
export default function Layout() {
  const { store } = useStore()
  const { session } = useAuth()

  return (
    <>
      <header className="topbar">
        <strong>{store?.name}</strong>
        <nav>
          <NavLink to="/products">المنتجات</NavLink>
          <NavLink to="/offers">العروض</NavLink>
          <NavLink to="/import">رفع المخزون</NavLink>
          <NavLink to="/settings">الإعدادات</NavLink>
        </nav>
        <span className="spacer" />
        <span className="muted" dir="ltr">{session?.user.email}</span>
        <button type="button" className="link" onClick={signOut}>خروج</button>
      </header>
      <main className="shell">
        <Outlet />
      </main>
    </>
  )
}
