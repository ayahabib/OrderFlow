-- Products, variants, inventory movements (DATABASE.md 3.3)

create table products (
  id           uuid primary key default gen_random_uuid(),
  store_id     uuid not null references stores(id) on delete cascade,
  name         text not null,
  search_text  text,                         -- normalized Arabic name for search
  aliases      text[] not null default '{}',
  description  text,
  category     text,
  price        numeric(10,2) not null check (price >= 0),
  is_active    boolean not null default true,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  unique (store_id, name)
);

create index products_store_idx on products (store_id);
create index products_search_trgm on products using gin (search_text gin_trgm_ops);

create table product_variants (
  id                   uuid primary key default gen_random_uuid(),
  store_id             uuid not null references stores(id) on delete cascade,
  product_id           uuid not null references products(id) on delete cascade,
  name                 text not null default 'الافتراضي',
  options              jsonb not null default '{}',
  sku                  text,
  price_override       numeric(10,2) check (price_override is null or price_override >= 0),
  stock_quantity       integer not null default 0 check (stock_quantity >= 0),
  low_stock_threshold  integer not null default 5,
  is_active            boolean not null default true,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now(),
  unique (product_id, name)
);

create index variants_product_idx on product_variants (product_id);
create index variants_store_idx on product_variants (store_id);
create unique index variants_sku_idx on product_variants (store_id, sku) where sku is not null;

create table inventory_movements (
  id          uuid primary key default gen_random_uuid(),
  store_id    uuid not null references stores(id) on delete cascade,
  variant_id  uuid not null references product_variants(id) on delete cascade,
  change      integer not null,          -- negative = decrease
  reason      movement_reason not null,
  order_id    uuid,
  created_at  timestamptz not null default now()
);

create index movements_variant_time_idx on inventory_movements (variant_id, created_at);
