-- Country code used to read customers' local phone numbers (task 3.9), e.g. 0599... -> +970599...

alter table stores
  add column phone_country_code text not null default '970'
  check (phone_country_code ~ '^[1-9][0-9]{0,2}$');
