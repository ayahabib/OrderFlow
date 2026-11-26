-- Audit rows get the real time of the insert (clock_timestamp), not the transaction start (now()),
-- so several changes in one transaction keep their order (task 3.14).
alter table audit_logs alter column created_at set default clock_timestamp();
