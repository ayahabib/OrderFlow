-- Backfill customers.phone_normalized for existing rows, same rules as app/utils/phone.py.
-- (Stored phones come from the order flow, which already turns Arabic digits into Latin ones.)

with cleaned as (
  select cu.id, s.phone_country_code as cc, regexp_replace(cu.phone, '[[:space:]().-]', '', 'g') as d
    from customers cu join stores s on s.id = cu.store_id
   where cu.phone is not null and cu.phone_normalized is null
), built as (
  select id,
         case
           when d like '+%'  then d
           when d like '00%' then '+' || substr(d, 3)
           when d like '0%'  then '+' || cc || substr(d, 2)
           when d like cc || '%' and length(d) > length(cc) + 7 then '+' || d
           else '+' || cc || d
         end as e164
    from cleaned
)
update customers cu
   set phone_normalized = b.e164
  from built b
 where cu.id = b.id and b.e164 ~ '^\+[1-9][0-9]{7,14}$';
