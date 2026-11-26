-- Two more order statuses (task 3.11): 'shipped' between in_progress and delivered,
-- and 'returned' (only after delivered; the items go back to stock).
alter type order_status add value if not exists 'shipped' after 'in_progress';
alter type order_status add value if not exists 'returned';
