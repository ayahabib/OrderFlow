/**
 * A ranked list of thin horizontal bars in plain HTML: name, bar, value.
 * Used where a few named items are compared (stock per product, top products): right-to-left
 * by default, every value printed next to its bar, and readable without hovering.
 */
type Item = { key: string; label: string; value: number; valueLabel?: string; title?: string }

export default function BarList({ items, ariaLabel }: { items: Item[]; ariaLabel: string }) {
  const max = Math.max(1, ...items.map((i) => i.value))
  return (
    <ul className="bar-list" aria-label={ariaLabel}>
      {items.map((i) => (
        <li key={i.key} title={i.title}>
          <span className="bar-name">{i.label}</span>
          <span className="bar-track" aria-hidden="true">
            <span className="bar-fill" style={{ width: `${(i.value / max) * 100}%` }} />
          </span>
          <span className="bar-value">{i.valueLabel ?? i.value}</span>
        </li>
      ))}
    </ul>
  )
}
