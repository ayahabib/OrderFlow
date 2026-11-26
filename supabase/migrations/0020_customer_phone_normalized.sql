-- Customers' phone in E.164 (task 3.9): rows with the same number are shown as one customer.
-- Written by the backend (app/utils/phone.py) when an order saves the phone.

alter table customers add column phone_normalized text;

create index customers_store_phone_idx on customers (store_id, phone_normalized)
  where phone_normalized is not null;
