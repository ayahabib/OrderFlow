import StoreForm from '../components/StoreForm'
import { signOut } from '../auth/AuthProvider'
import { createStore } from '../lib/api'
import { useStore } from '../store/StoreProvider'

/** Shown once, right after the first sign-up, until the owner creates their store. */
export default function CreateStorePage() {
  const { setStore } = useStore()

  return (
    <main className="auth-page">
      <div className="stack">
        <h1>أهلاً! خلّينا ننشئ متجرك</h1>
        <p className="muted">بتقدر تعدّل هالمعلومات بعدين من صفحة الإعدادات.</p>
        <StoreForm
          initial={{ name: '', phone: null, currency: 'ILS' }}
          submitLabel="إنشاء المتجر"
          onSubmit={async (input) => setStore(await createStore(input))}
        />
        <button type="button" className="link" onClick={signOut}>تسجيل الخروج</button>
      </div>
    </main>
  )
}
