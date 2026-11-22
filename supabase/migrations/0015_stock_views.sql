-- Stock views for the stock page (DATABASE.md section 7, task 3.2).
-- security_invoker: the views run with the caller's rights, so RLS still applies.

create view low_stock_variants with (security_invoker = true) as
select v.id as variant_id, v.store_id, p.id as product_id, p.name as product_name, v.name as variant_name,
       v.stock_quantity, v.low_stock_threshold
  from product_variants v
  join products p on p.id = v.product_id and p.store_id = v.store_id
 where v.is_active and p.is_active
   and v.stock_quantity <= v.low_stock_threshold;

create view product_stock with (security_invoker = true) as
select p.id as product_id, p.store_id, p.name,
       count(v.id)            as variants_count,
       sum(v.stock_quantity)  as total_stock
  from products p
  join product_variants v on v.product_id = p.id and v.store_id = p.store_id and v.is_active
 where p.is_active
 group by p.id, p.store_id, p.name;
