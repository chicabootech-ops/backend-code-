-- Adds the 'packed' and 'out_for_delivery' statuses. Their notification
-- templates were seeded in 000041 but orders_status_check never permitted the
-- statuses, so both were unreachable.

ALTER TABLE commerce.orders
  DROP CONSTRAINT IF EXISTS orders_status_check;

ALTER TABLE commerce.orders
  ADD CONSTRAINT orders_status_check CHECK (
    status IN (
      'pending',
      'confirmed',
      'processing',
      'packed',
      'shipped',
      'out_for_delivery',
      'delivered',
      'completed',
      'cancelled',
      'returned',
      'refunded'
    )
  );

CREATE INDEX IF NOT EXISTS orders_tracking_number_idx
  ON commerce.orders ((metadata ->> 'tracking_number'))
  WHERE metadata ->> 'tracking_number' IS NOT NULL;

COMMENT ON INDEX commerce.orders_tracking_number_idx IS
  'Lookup by courier tracking number. Partial — only shipped orders have one.';
