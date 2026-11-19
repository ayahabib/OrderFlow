-- Order snapshots of applied offers (DATABASE.md 3.5 note), used by the order flow (task 1.8).
-- order_items.unit_price = price after the offer; list_price = price before it.

alter table order_items
  add column list_price numeric(10,2),
  add column offer_id   uuid references offers(id) on delete set null;

alter table orders
  add column free_delivery_offer_id uuid references offers(id) on delete set null;
