-- Returned orders are not sales (task 3.11). Same view as 0014 with one more excluded status.
create or replace view daily_sales with (security_invoker = true) as
select o.store_id,
       (o.created_at at time zone s.timezone)::date as day,
       count(*)                                      as orders_count,
       sum(o.total)                                  as revenue
  from orders o
  join stores s on s.id = o.store_id
 where o.status not in ('cancelled', 'returned')
 group by o.store_id, (o.created_at at time zone s.timezone)::date;
