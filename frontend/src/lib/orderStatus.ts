import type { OrderStatus } from './api'

/** Badge for each order status (orders page, customers page). */
export const ORDER_STATUS: Record<OrderStatus, { label: string; cls: string }> = {
  new: { label: 'جديد', cls: 'status-new' },
  in_progress: { label: 'قيد التجهيز', cls: 'warn' },
  shipped: { label: 'تم الشحن', cls: 'warn' },
  delivered: { label: 'تم التوصيل', cls: '' },
  cancelled: { label: 'ملغي', cls: 'muted-badge' },
  returned: { label: 'مرتجع', cls: 'muted-badge' },
}

/** Orders that are not sales: shown faded. */
export const isClosedWithoutSale = (s: OrderStatus) => s === 'cancelled' || s === 'returned'
