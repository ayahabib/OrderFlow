import StoreForm from '../components/StoreForm'
import { updateStore } from '../lib/api'
import { useStore } from '../store/StoreProvider'

export default function SettingsPage() {
  const { store, setStore } = useStore()
  if (!store) return null

  return (
    <section className="stack">
      <h1>الإعدادات</h1>
      <StoreForm
        key={store.id}
        initial={{ name: store.name, phone: store.phone, currency: store.currency }}
        submitLabel="حفظ"
        onSubmit={async (input) => setStore(await updateStore(input))}
      />
    </section>
  )
}
