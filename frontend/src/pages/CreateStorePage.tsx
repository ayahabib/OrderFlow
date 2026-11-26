import { useState } from 'react'
import StoreForm from '../components/StoreForm'
import ZonesEditor from '../components/ZonesEditor'
import { signOut } from '../auth/AuthProvider'
import { createStore, type Store } from '../lib/api'
import { useStore } from '../store/StoreProvider'

/**
 * Shown once, right after the first sign-up.
 * Step 1 creates the store. Step 2 (optional) asks where the store delivers.
 * The dashboard opens only after step 2, so the owner sees the zones question.
 */
export default function CreateStorePage() {
  const { setStore } = useStore()
  const [created, setCreated] = useState<Store | null>(null)
  const [zoneCount, setZoneCount] = useState(0)

  if (!created) {
    return (
      <main className="auth-page single">
        <div className="stack">
          <p className="muted small">خطوة 1 من 2</p>
          <h1>أهلاً! خلّينا ننشئ متجرك</h1>
          <p className="muted">بتقدر تعدّل هالمعلومات بعدين من صفحة الإعدادات.</p>
          <StoreForm
            initial={{ name: '', phone: null, currency: 'ILS', phone_country_code: '970' }}
            submitLabel="التالي"
            onSubmit={async (input) => setCreated(await createStore(input))}
          />
          <button type="button" className="link" onClick={signOut}>تسجيل الخروج</button>
        </div>
      </main>
    )
  }

  return (
    <main className="auth-page single">
      <div className="stack onboarding-wide">
        <p className="muted small">خطوة 2 من 2</p>
        <h1>وين بتوصّلوا؟</h1>
        <p className="muted">
          ضيف المناطق الي بتوصّل عليها وأجرة كل وحدة. البوت رح يجاوب العملاء ويحسب التوصيل من هالقائمة بس،
          وأي منطقة مش موجودة رح تتحوّل إلك. بتقدر تعدّلها بعدين من الإعدادات.
        </p>
        <div className="card wide">
          <ZonesEditor currency={created.currency} onChange={(z) => setZoneCount(z.length)} />
        </div>
        <button type="button" onClick={() => setStore(created)}>
          {zoneCount > 0 ? 'إنهاء والدخول للمتجر' : 'تخطّي هلأ'}
        </button>
      </div>
    </main>
  )
}
