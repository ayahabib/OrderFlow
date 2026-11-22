-- Daily sales for the dashboard (DATABASE.md section 7, task 3.1).
-- One change from the doc's sketch: the day is the store's local day (stores.timezone),
-- like the orders page, so an order at 1 AM doesn't land on the previous day (UTC).
-- security_invoker: the view runs with the caller's rights, so RLS still applies.

create view daily_sales with (security_invoker = true) as
select o.store_id,
       (o.created_at at time zone s.timezone)::date as day,
       count(*)                                      as orders_count,
       sum(o.total)                                  as revenue
  from orders o
  join stores s on s.id = o.store_id
 where o.status <> 'cancelled'
 group by o.store_id, (o.created_at at time zone s.timezone)::date;
