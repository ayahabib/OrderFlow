-- Row Level Security on every table (DATABASE.md 6)
-- Note: the backend's service-role connection bypasses RLS, so backend queries
-- must still filter by store_id in code.

alter table stores enable row level security;
create policy "owner_access" on stores for all
  using (owner_id = auth.uid()) with check (owner_id = auth.uid());

do $$
declare t text;
begin
  foreach t in array array[
    'store_channels','products','product_variants','inventory_movements',
    'delivery_zones','customers','conversations','messages','orders',
    'order_items','escalations','knowledge_base'
  ] loop
    execute format('alter table %I enable row level security', t);
    execute format(
      'create policy "store_isolation" on %I for all
         using (store_id = current_store_id())
         with check (store_id = current_store_id())', t);
  end loop;
end $$;
