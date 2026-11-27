-- Admin actions are written to audit_logs with actor = 'admin' (task 5.2).
alter type audit_actor add value if not exists 'admin';
