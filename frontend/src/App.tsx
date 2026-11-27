import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AuthProvider, useAuth } from './auth/AuthProvider'
import ActivityPage from './pages/ActivityPage'
import AdminLayout from './components/AdminLayout'
import AdminOverviewPage from './pages/AdminOverviewPage'
import AdminStoresPage from './pages/AdminStoresPage'
import AdminWhatsAppPage from './pages/AdminWhatsAppPage'
import Layout from './components/Layout'
import ConversationsPage from './pages/ConversationsPage'
import CreateStorePage from './pages/CreateStorePage'
import CustomersPage from './pages/CustomersPage'
import DashboardPage from './pages/DashboardPage'
import KnowledgePage from './pages/KnowledgePage'
import OrdersPage from './pages/OrdersPage'
import ProductsPage from './pages/ProductsPage'
import LoginPage from './pages/LoginPage'
import SettingsPage from './pages/SettingsPage'
import SimulatorPage from './pages/SimulatorPage'
import StockPage from './pages/StockPage'
import TrackPage from './pages/TrackPage'
import ResetPasswordPage from './pages/ResetPasswordPage'
import { StoreProvider, useStore } from './store/StoreProvider'

function Loading() {
  return <main className="auth-page single"><p className="muted">جاري التحميل...</p></main>
}

/** Logged in: create the store first if there is none, otherwise show the dashboard. */
function Dashboard() {
  const { store, isAdmin, loading, error, reload } = useStore()
  if (loading) return <Loading />
  if (error) {
    return (
      <main className="auth-page single">
        <div className="stack">
          <p className="msg error" role="alert">{error}</p>
          <button type="button" onClick={reload}>إعادة المحاولة</button>
        </div>
      </main>
    )
  }
  // A platform admin account (5.2) sees the admin pages only, with or without a store.
  if (isAdmin) {
    return (
      <Routes>
        <Route element={<AdminLayout />}>
          <Route path="/admin" element={<AdminOverviewPage />} />
          <Route path="/admin/stores" element={<AdminStoresPage />} />
          <Route path="/admin/whatsapp" element={<AdminWhatsAppPage />} />
          <Route path="*" element={<Navigate to="/admin" replace />} />
        </Route>
      </Routes>
    )
  }
  if (!store) return <CreateStorePage />

  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/orders" element={<OrdersPage />} />
        <Route path="/customers" element={<CustomersPage />} />
        <Route path="/customers/:id" element={<CustomersPage />} />
        <Route path="/conversations" element={<ConversationsPage />} />
        <Route path="/conversations/:id" element={<ConversationsPage />} />
        <Route path="/products" element={<ProductsPage />} />
        <Route path="/stock" element={<StockPage />} />
        <Route path="/offers" element={<Navigate to="/products#offers" replace />} />
        <Route path="/knowledge" element={<KnowledgePage />} />
        <Route path="/import" element={<Navigate to="/products" replace />} />
        <Route path="/simulator" element={<SimulatorPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/activity" element={<ActivityPage />} />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Route>
    </Routes>
  )
}

function Root() {
  const { session, loading } = useAuth()
  if (loading) return <Loading />
  if (!session) return <LoginPage />
  return (
    <StoreProvider>
      <Dashboard />
    </StoreProvider>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          {/* Public: the customer's order tracking link (3.6), no login. */}
          <Route path="/track/:token" element={<TrackPage />} />
          {/* Password reset link from the email (3.15): works signed in or not. */}
          <Route path="/reset-password" element={<ResetPasswordPage />} />
          <Route path="*" element={<Root />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  )
}
