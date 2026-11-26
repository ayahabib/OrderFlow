import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { supabase } from '../lib/supabase'

/**
 * Set a new password (task 3.15). The emailed link signs the user in for this one purpose;
 * supabase-js reads the token from the URL on load. Without that session the link is old or used.
 */
export default function ResetPasswordPage() {
  const navigate = useNavigate()
  const [ready, setReady] = useState<boolean | null>(null)
  const [password, setPassword] = useState('')
  const [again, setAgain] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)

  useEffect(() => {
    const { data } = supabase.auth.onAuthStateChange((event, session) => {
      if (event === 'PASSWORD_RECOVERY' || session) setReady(true)
    })
    // The link may already have been read before this page subscribed.
    supabase.auth.getSession().then(({ data: s }) => setReady((r) => r || Boolean(s.session)))
    const t = window.setTimeout(() => setReady((r) => r ?? false), 4000)
    return () => { data.subscription.unsubscribe(); window.clearTimeout(t) }
  }, [])

  async function submit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    if (password.length < 6) return setError('كلمة السر لازم تكون 6 أحرف على الأقل')
    if (password !== again) return setError('الكلمتين مش متطابقات')
    setBusy(true)
    try {
      const { error } = await supabase.auth.updateUser({ password })
      if (error) throw error
      setDone(true)
      window.setTimeout(() => navigate('/dashboard', { replace: true }), 1500)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="auth-page single">
      <div className="stack">
        <h1>كلمة سر جديدة</h1>
        {ready === null && <p className="muted">جاري التحقق من الرابط...</p>}
        {ready === false && (
          <p className="msg error" role="alert">
            الرابط منتهي أو مستعمل. ارجع لصفحة الدخول واكبس "نسيت كلمة السر؟" مرة ثانية.
          </p>
        )}
        {ready && !done && (
          <form className="card" onSubmit={submit}>
            <label>
              كلمة السر الجديدة
              <input type="password" dir="ltr" required minLength={6} autoComplete="new-password"
                value={password} onChange={(e) => setPassword(e.target.value)} />
            </label>
            <label>
              أعد كتابتها
              <input type="password" dir="ltr" required minLength={6} autoComplete="new-password"
                value={again} onChange={(e) => setAgain(e.target.value)} />
            </label>
            {error && <p className="msg error" role="alert">{error}</p>}
            <button type="submit" disabled={busy}>{busy ? '...' : 'حفظ'}</button>
          </form>
        )}
        {done && <p className="msg info">تم تغيير كلمة السر ✓ عم نوديك على المتجر...</p>}
        <a href="/" className="link small">رجوع لصفحة الدخول</a>
      </div>
    </main>
  )
}
