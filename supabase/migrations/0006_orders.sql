-- Orders and order items (DATABASE.md 3.5)

create table orders (
  id               uuid primary key default gen_random_uuid(),
  store_id         uuid not null references stores(id) on delete cascade,
  order_number     integer not null,             -- per-store sequence shown to the customer
  customer_id      uuid not null references customers(id),
  conversation_id  uuid references conversations(id),
  status           order_status not null default 'new',
  delivery_address text,
  delivery_zone_id uuid references delivery_zones(id),
  delivery_fee     numeric(10,2) not null default 0,
  subtotal         numeric(10,2) not null,
  total            numeric(10,2) not null,
  notes            text,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  unique (store_id, order_number)
);

create index orders_store_status_idx on orders (store_id, status, created_at desc);

create table order_items (
  id            uuid primary key default gen_random_uuid(),
  store_id      uuid not null references stores(id) on delete cascade,
  order_id      uuid not null references orders(id) on delete cascade,
  product_id    uuid references products(id) on delete set null,
  variant_id    uuid references product_variants(id) on delete set null,
  product_name  text not null,                   -- snapshot
  variant_name  text,                            -- snapshot
  unit_price    numeric(10,2) not null,          -- snapshot
  quantity      integer not null check (quantity > 0),
  notes         text
);

create index order_items_order_idx on order_items (order_id);
