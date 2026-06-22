-- =============================================================================
-- Migration: 000025_rls_policies_v3
-- Chic A Boo — Row Level Security for commerce customer tables (Architecture v3.0)
-- Depends on: 000012–000020, 000017 (payment_customers in commerce)
-- Blocks:     000026
--
-- Enables RLS + policies on customer-owned commerce tables.
-- Catalog, inventory, payments (except payment_customers SELECT),
-- coupons, shipments, and all ops tables remain service-role only.
--
-- Session variable: SET LOCAL app.current_user_id = '<uuid>';
-- Function: public.current_user_id()
-- =============================================================================


-- =============================================================================
-- commerce.carts
-- Authenticated users only; guest carts are service-role managed.
-- =============================================================================
ALTER TABLE commerce.carts ENABLE ROW LEVEL SECURITY;

CREATE POLICY carts_select_own ON commerce.carts
  FOR SELECT
  USING (
    user_id = public.current_user_id()
    AND deleted_at IS NULL
  );

COMMENT ON POLICY carts_select_own ON commerce.carts IS
  'Users read their own active carts.';

CREATE POLICY carts_insert_own ON commerce.carts
  FOR INSERT
  WITH CHECK (user_id = public.current_user_id());

COMMENT ON POLICY carts_insert_own ON commerce.carts IS
  'Users create carts owned by themselves.';

CREATE POLICY carts_update_own ON commerce.carts
  FOR UPDATE
  USING (user_id = public.current_user_id())
  WITH CHECK (user_id = public.current_user_id());

COMMENT ON POLICY carts_update_own ON commerce.carts IS
  'Users update their own carts.';


-- =============================================================================
-- commerce.cart_items — access via cart ownership
-- =============================================================================
ALTER TABLE commerce.cart_items ENABLE ROW LEVEL SECURITY;

CREATE POLICY cart_items_select_own ON commerce.cart_items
  FOR SELECT
  USING (
    EXISTS (
      SELECT 1
      FROM commerce.carts AS c
      WHERE c.id = cart_items.cart_id
        AND c.user_id = public.current_user_id()
        AND c.deleted_at IS NULL
    )
    AND deleted_at IS NULL
  );

CREATE POLICY cart_items_insert_own ON commerce.cart_items
  FOR INSERT
  WITH CHECK (
    EXISTS (
      SELECT 1
      FROM commerce.carts AS c
      WHERE c.id = cart_items.cart_id
        AND c.user_id = public.current_user_id()
        AND c.deleted_at IS NULL
    )
  );

CREATE POLICY cart_items_update_own ON commerce.cart_items
  FOR UPDATE
  USING (
    EXISTS (
      SELECT 1
      FROM commerce.carts AS c
      WHERE c.id = cart_items.cart_id
        AND c.user_id = public.current_user_id()
    )
  )
  WITH CHECK (
    EXISTS (
      SELECT 1
      FROM commerce.carts AS c
      WHERE c.id = cart_items.cart_id
        AND c.user_id = public.current_user_id()
    )
  );

CREATE POLICY cart_items_delete_own ON commerce.cart_items
  FOR DELETE
  USING (
    EXISTS (
      SELECT 1
      FROM commerce.carts AS c
      WHERE c.id = cart_items.cart_id
        AND c.user_id = public.current_user_id()
    )
  );


-- =============================================================================
-- commerce.wishlist_items
-- =============================================================================
ALTER TABLE commerce.wishlist_items ENABLE ROW LEVEL SECURITY;

CREATE POLICY wishlist_items_select_own ON commerce.wishlist_items
  FOR SELECT
  USING (user_id = public.current_user_id());

CREATE POLICY wishlist_items_insert_own ON commerce.wishlist_items
  FOR INSERT
  WITH CHECK (user_id = public.current_user_id());

CREATE POLICY wishlist_items_delete_own ON commerce.wishlist_items
  FOR DELETE
  USING (user_id = public.current_user_id());


-- =============================================================================
-- commerce.orders — SELECT own orders only (immutable; no customer writes)
-- =============================================================================
ALTER TABLE commerce.orders ENABLE ROW LEVEL SECURITY;

CREATE POLICY orders_select_own ON commerce.orders
  FOR SELECT
  USING (user_id = public.current_user_id());

COMMENT ON POLICY orders_select_own ON commerce.orders IS
  'Customers view their own order history.';


-- =============================================================================
-- commerce.order_items — SELECT via order ownership
-- =============================================================================
ALTER TABLE commerce.order_items ENABLE ROW LEVEL SECURITY;

CREATE POLICY order_items_select_own ON commerce.order_items
  FOR SELECT
  USING (
    EXISTS (
      SELECT 1
      FROM commerce.orders AS o
      WHERE o.id = order_items.order_id
        AND o.user_id = public.current_user_id()
    )
  );


-- =============================================================================
-- commerce.returns — SELECT own returns
-- =============================================================================
ALTER TABLE commerce.returns ENABLE ROW LEVEL SECURITY;

CREATE POLICY returns_select_own ON commerce.returns
  FOR SELECT
  USING (
    user_id = public.current_user_id()
    AND deleted_at IS NULL
  );


-- =============================================================================
-- commerce.reviews
-- Public reads approved; authors see own pending; authors insert/update own
-- =============================================================================
ALTER TABLE commerce.reviews ENABLE ROW LEVEL SECURITY;

CREATE POLICY reviews_select_public_or_own ON commerce.reviews
  FOR SELECT
  USING (
    (
      status = 'approved'
      AND deleted_at IS NULL
    )
    OR (
      user_id = public.current_user_id()
      AND deleted_at IS NULL
    )
  );

CREATE POLICY reviews_insert_own ON commerce.reviews
  FOR INSERT
  WITH CHECK (user_id = public.current_user_id());

CREATE POLICY reviews_update_own ON commerce.reviews
  FOR UPDATE
  USING (
    user_id = public.current_user_id()
    AND status IN ('pending', 'approved')
  )
  WITH CHECK (user_id = public.current_user_id());


-- =============================================================================
-- commerce.payment_customers — SELECT own (relocated from public in 000017)
-- =============================================================================
ALTER TABLE commerce.payment_customers ENABLE ROW LEVEL SECURITY;

CREATE POLICY payment_customers_select_own ON commerce.payment_customers
  FOR SELECT
  USING (
    user_id = public.current_user_id()
    AND deleted_at IS NULL
  );


-- =============================================================================
-- DOCUMENTATION: tables intentionally WITHOUT RLS (service role only)
-- =============================================================================
COMMENT ON SCHEMA commerce IS
  'Transactional e-commerce schema. RLS enabled on customer-owned tables only '
  '(carts, cart_items, wishlist_items, orders, order_items, returns, reviews, '
  'payment_customers). Catalog, inventory, payments, coupons, and shipping '
  'tables are accessed via chicaboo_service role with RLS bypass.';


-- =============================================================================
-- VERIFICATION QUERIES (manual)
-- =============================================================================
--
-- 1. RLS enabled on target tables:
--    SELECT c.relname, c.relrowsecurity
--    FROM pg_class c
--    JOIN pg_namespace n ON n.oid = c.relnamespace
--    WHERE n.nspname = 'commerce'
--      AND c.relname IN (
--        'carts', 'cart_items', 'wishlist_items', 'orders',
--        'order_items', 'returns', 'reviews', 'payment_customers'
--      )
--    ORDER BY c.relname;
--    -- Expect: relrowsecurity = true for all
--
-- 2. Policy count:
--    SELECT c.relname, COUNT(p.polname) AS policies
--    FROM pg_class c
--    JOIN pg_namespace n ON n.oid = c.relnamespace
--    LEFT JOIN pg_policy p ON p.polrelid = c.oid
--    WHERE n.nspname = 'commerce'
--      AND c.relname IN (
--        'carts', 'cart_items', 'wishlist_items', 'orders',
--        'order_items', 'returns', 'reviews', 'payment_customers'
--      )
--    GROUP BY c.relname
--    ORDER BY c.relname;
--
-- 3. Catalog tables RLS off:
--    SELECT c.relname, c.relrowsecurity
--    FROM pg_class c
--    JOIN pg_namespace n ON n.oid = c.relnamespace
--    WHERE n.nspname = 'commerce' AND c.relname = 'products';
--    -- Expect: false
--
-- 4. ops schema RLS off:
--    SELECT c.relname, c.relrowsecurity
--    FROM pg_class c
--    JOIN pg_namespace n ON n.oid = c.relnamespace
--    WHERE n.nspname = 'ops';
--    -- Expect: all false
--
-- 5. Migration recorded:
--    SELECT version FROM public.schema_migrations
--    WHERE version = '000025_rls_policies_v3';


-- =============================================================================
-- ROLLBACK SQL (manual)
-- =============================================================================
--
-- DROP POLICY IF EXISTS payment_customers_select_own ON commerce.payment_customers;
-- ALTER TABLE commerce.payment_customers DISABLE ROW LEVEL SECURITY;
--
-- DROP POLICY IF EXISTS reviews_update_own ON commerce.reviews;
-- DROP POLICY IF EXISTS reviews_insert_own ON commerce.reviews;
-- DROP POLICY IF EXISTS reviews_select_public_or_own ON commerce.reviews;
-- ALTER TABLE commerce.reviews DISABLE ROW LEVEL SECURITY;
--
-- DROP POLICY IF EXISTS returns_select_own ON commerce.returns;
-- ALTER TABLE commerce.returns DISABLE ROW LEVEL SECURITY;
--
-- DROP POLICY IF EXISTS order_items_select_own ON commerce.order_items;
-- ALTER TABLE commerce.order_items DISABLE ROW LEVEL SECURITY;
--
-- DROP POLICY IF EXISTS orders_select_own ON commerce.orders;
-- ALTER TABLE commerce.orders DISABLE ROW LEVEL SECURITY;
--
-- DROP POLICY IF EXISTS wishlist_items_delete_own ON commerce.wishlist_items;
-- DROP POLICY IF EXISTS wishlist_items_insert_own ON commerce.wishlist_items;
-- DROP POLICY IF EXISTS wishlist_items_select_own ON commerce.wishlist_items;
-- ALTER TABLE commerce.wishlist_items DISABLE ROW LEVEL SECURITY;
--
-- DROP POLICY IF EXISTS cart_items_delete_own ON commerce.cart_items;
-- DROP POLICY IF EXISTS cart_items_update_own ON commerce.cart_items;
-- DROP POLICY IF EXISTS cart_items_insert_own ON commerce.cart_items;
-- DROP POLICY IF EXISTS cart_items_select_own ON commerce.cart_items;
-- ALTER TABLE commerce.cart_items DISABLE ROW LEVEL SECURITY;
--
-- DROP POLICY IF EXISTS carts_update_own ON commerce.carts;
-- DROP POLICY IF EXISTS carts_insert_own ON commerce.carts;
-- DROP POLICY IF EXISTS carts_select_own ON commerce.carts;
-- ALTER TABLE commerce.carts DISABLE ROW LEVEL SECURITY;
--
-- DELETE FROM public.schema_migrations
-- WHERE version = '000025_rls_policies_v3';
