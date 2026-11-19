import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AuthProvider, useAuth } from './auth/AuthProvider'
import Layout from './components/Layout'
import CreateStorePage from './pages/CreateStorePage'
import ImportPage from './pages/ImportPage'
import OffersPage from './pages/OffersPage'
import ProductsPage from './pages/ProductsPage'
import LoginPage from './pages/LoginPage'
import SettingsPage from './pages/SettingsPage'
import SimulatorPage from './pages/SimulatorPage'
import { StoreProvider, useStore } from './store/StoreProvider'

function Loading() {
  return <main className="auth-page"><p className="muted">جاري التحميل...</p></main>
}

/** Logged in: create the store first if there is none, otherwise show the dashboard. */
function Dashboard() {
  const { store, loading, error, reload } = useStore()
  if (loading) return <Loading />
  if (error) {
    return (
      <main className="auth-page">
        <div className="stack">
          <p className="msg error" role="alert">{error}</p>
          <button type="button" onClick={reload}>إعادة المحاولة</button>
        </div>
      </main>
    )
  }
  if (!store) return <CreateStorePage />

  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/products" element={<ProductsPage />} />
        <Route path="/offers" element={<OffersPage />} />
        <Route path="/import" element={<ImportPage />} />
        <Route path="/simulator" element={<SimulatorPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/products" replace />} />
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
        <Root />
      </BrowserRouter>
    </AuthProvider>
  )
}
