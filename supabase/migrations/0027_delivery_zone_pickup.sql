-- "Pickup from the store" (task 3.15): a zone with fee 0 that needs no delivery address.
-- At most one per store.
alter table delivery_zones add column is_pickup boolean not null default false;
create unique index delivery_zones_one_pickup_idx on delivery_zones (store_id) where is_pickup;
