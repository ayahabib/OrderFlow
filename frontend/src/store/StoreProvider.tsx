import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { useAuth } from '../auth/AuthProvider'
import { getMe, getStore, type Store } from '../lib/api'

type StoreState = {
  store: Store | null
  loading: boolean
  error: string | null
  setStore: (store: Store) => void
  reload: () => void
}

const StoreContext = createContext<StoreState | null>(null)

/** Loads the logged-in owner's store (or null if they haven't created one yet). */
export function StoreProvider({ children }: { children: ReactNode }) {
  const { session } = useAuth()
  const [store, setStore] = useState<Store | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const me = await getMe()
      setStore(me.store_id ? await getStore() : null)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  const userId = session?.user.id
  useEffect(() => {
    if (userId) load()
  }, [userId, load])

  return (
    <StoreContext.Provider value={{ store, loading, error, setStore, reload: load }}>
      {children}
    </StoreContext.Provider>
  )
}

export function useStore(): StoreState {
  const ctx = useContext(StoreContext)
  if (!ctx) throw new Error('useStore must be used inside StoreProvider')
  return ctx
}
