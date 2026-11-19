-- Delivery zones (DATABASE.md 3.3)

create table delivery_zones (
  id        uuid primary key default gen_random_uuid(),
  store_id  uuid not null references stores(id) on delete cascade,
  name      text not null,
  fee       numeric(10,2) not null check (fee >= 0),
  is_active boolean not null default true,
  unique (store_id, name)
);
