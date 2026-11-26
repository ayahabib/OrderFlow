-- Stock that comes back when a delivered order is returned (task 3.11).
alter type movement_reason add value if not exists 'order_return';
