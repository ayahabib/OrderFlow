-- "Tell me when it's back" (task 3.12): a customer asked to hear when a sold-out variant returns.
-- Saved only after the customer says yes. One open row per customer and variant;
-- notified_at is set when the owner sends the "it's back" message.

create table customer_interests (
  id           uuid primary key default gen_random_uuid(),
  store_id     uuid not null references stores(id) on delete cascade,
  customer_id  uuid not null references customers(id) on delete cascade,
  variant_id   uuid not null references product_variants(id) on delete cascade,
  created_at   timestamptz not null default now(),
  notified_at  timestamptz
);

create unique index customer_interests_open_idx on customer_interests (store_id, customer_id, variant_id)
  where notified_at is null;
create index customer_interests_variant_idx on customer_interests (store_id, variant_id) where notified_at is null;

alter table customer_interests enable row level security;
create policy "store_isolation" on customer_interests for all
  using (store_id = current_store_id())
  with check (store_id = current_store_id());
