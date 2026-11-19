import { useState, type FormEvent } from 'react'
import { supabase } from '../lib/supabase'

type Mode = 'login' | 'register'

/** Map common Supabase auth errors to Arabic. */
function authError(message: string): string {
  if (/invalid login credentials/i.test(message)) return 'الإيميل أو كلمة السر غلط'
  if (/email not confirmed/i.test(message)) return 'لازم تأكد إيميلك أول (افتح رابط التأكيد)'
  if (/already registered/i.test(message)) return 'هالإيميل مسجّل من قبل، سجّل دخول'
  if (/password should be at least/i.test(message)) return 'كلمة السر لازم تكون 6 أحرف على الأقل'
  if (/rate limit/i.test(message)) return 'محاولات كتير، استنى شوي وجرّب'
  return message
}

export default function LoginPage() {
  const [mode, setMode] = useState<Mode>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [info, setInfo] = useState<string | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    setInfo(null)
    try {
      if (mode === 'login') {
        const { error } = await supabase.auth.signInWithPassword({ email, password })
        if (error) throw error
      } else {
        const { data, error } = await supabase.auth.signUp({ email, password })
        if (error) throw error
        // With "Confirm email" on, Supabase returns no session until the link is clicked.
        if (!data.session) setInfo('بعتنالك رابط تأكيد على الإيميل. افتحه وبعدين سجّل دخول.')
      }
    } catch (err) {
      setError(authError((err as Error).message))
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="auth-page">
      <form className="card" onSubmit={submit}>
        <h1>{mode === 'login' ? 'تسجيل الدخول' : 'حساب جديد'}</h1>

        <label>
          الإيميل
          <input type="email" dir="ltr" required autoComplete="email"
            value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label>
          كلمة السر
          <input type="password" dir="ltr" required minLength={6}
            autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
            value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>

        {error && <p className="msg error" role="alert">{error}</p>}
        {info && <p className="msg info">{info}</p>}

        <button type="submit" disabled={busy}>
          {busy ? '...' : mode === 'login' ? 'دخول' : 'إنشاء الحساب'}
        </button>

        <button type="button" className="link"
          onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setError(null); setInfo(null) }}>
          {mode === 'login' ? 'ما عندك حساب؟ سجّل هون' : 'عندك حساب؟ سجّل دخول'}
        </button>
      </form>
    </main>
  )
}
