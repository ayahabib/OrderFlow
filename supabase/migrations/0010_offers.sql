-- Offers (DATABASE.md 3.7): percent off / fixed price on a product or variant, free delivery over a total.
-- Prices are computed in code (services/pricing.py), never by the LLM.

create type offer_kind as enum ('percent_off', 'fixed_price', 'free_delivery');

create table offers (
  id               uuid primary key default gen_random_uuid(),
  store_id         uuid not null references stores(id) on delete cascade,
  name             text not null,
  kind             offer_kind not null,
  product_id       uuid references products(id) on delete cascade,
  variant_id       uuid references product_variants(id) on delete cascade,
  percent          numeric(5,2) check (percent > 0 and percent < 100),
  fixed_price      numeric(10,2) check (fixed_price >= 0),
  min_order_total  numeric(10,2) check (min_order_total >= 0),
  starts_at        timestamptz,
  ends_at          timestamptz,
  is_active        boolean not null default true,
  created_at       timestamptz not null default now(),
  constraint offers_dates_check check (ends_at is null or starts_at is null or ends_at > starts_at),
  constraint offers_kind_check check (
    (kind = 'percent_off'   and percent is not null and fixed_price is null and min_order_total is null
                            and num_nonnulls(product_id, variant_id) = 1) or
    (kind = 'fixed_price'   and fixed_price is not null and percent is null and min_order_total is null
                            and num_nonnulls(product_id, variant_id) = 1) or
    (kind = 'free_delivery' and min_order_total is not null and percent is null and fixed_price is null
                            and product_id is null and variant_id is null)
  )
);

create index offers_store_idx on offers (store_id, is_active);

alter table offers enable row level security;
create policy "store_isolation" on offers for all
  using (store_id = current_store_id())
  with check (store_id = current_store_id());
