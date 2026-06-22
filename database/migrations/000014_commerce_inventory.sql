-- =============================================================================
-- Migration: 000014_commerce_inventory
-- Chic A Boo — multi-warehouse inventory (Architecture v3.0)
-- Depends on: 000013_commerce_products, 000013a_product_catalog_constraints
-- Blocks:     000015, 000016, 000018, 000026
--
-- Creates:
--   commerce.warehouses
--   commerce.inventory              — stock balances per warehouse × variant
--   commerce.inventory_movements    — append-only audit ledger
--   commerce.stock_reservations     — cart/order holds on variant stock
--   commerce.stock_transfers        — inter-warehouse transfer documents
--   commerce.fulfillment_allocations — warehouse picks per order line (FK deferred)
--
-- Also creates:
--   commerce.inventory_available    — view: on_hand − reserved per row
--
-- Deferred to 000016:
--   FK stock_reservations.order_id  → commerce.orders
--   FK stock_reservations.cart_id   → commerce.carts
--   FK fulfillment_allocations.order_item_id → commerce.order_items
--
-- Deferred to 000024 / 000026:
--   low-stock partial index; default warehouse seed (000026)
-- =============================================================================


-- =============================================================================
-- TABLE: commerce.warehouses
-- Physical or logical stocking locations. ERP-synced (ADR-010).
-- =============================================================================
CREATE TABLE commerce.warehouses (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  code                TEXT NOT NULL,
  name                TEXT NOT NULL,

  -- Structured address e.g. {"line1","city","state","pincode","country"}.
  address             JSONB NOT NULL DEFAULT '{}'::jsonb,

  is_active           BOOLEAN NOT NULL DEFAULT TRUE,
  is_default          BOOLEAN NOT NULL DEFAULT FALSE,
  priority            INTEGER NOT NULL DEFAULT 0,

  metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,

  external_id         TEXT,
  sync_status         TEXT,
  last_synced_at      TIMESTAMPTZ,

  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  deleted_at          TIMESTAMPTZ,

  CONSTRAINT warehouses_address_object_check CHECK (
    jsonb_typeof(address) = 'object'
  ),

  CONSTRAINT warehouses_sync_status_check CHECK (
    sync_status IS NULL
    OR sync_status IN ('pending', 'synced', 'failed')
  ),

  CONSTRAINT warehouses_priority_nonneg CHECK (priority >= 0)
);

COMMENT ON TABLE commerce.warehouses IS
  'Stocking locations for multi-warehouse inventory. Soft-deletable.';

COMMENT ON COLUMN commerce.warehouses.id IS 'UUID primary key.';
COMMENT ON COLUMN commerce.warehouses.code IS
  'Short unique code e.g. WH-MUM-01; unique among active warehouses.';
COMMENT ON COLUMN commerce.warehouses.name IS 'Human-readable warehouse name.';
COMMENT ON COLUMN commerce.warehouses.address IS 'Structured JSON address for shipping labels.';
COMMENT ON COLUMN commerce.warehouses.is_active IS
  'When false, warehouse is excluded from new allocations.';
COMMENT ON COLUMN commerce.warehouses.is_default IS
  'At most one default warehouse for first-party fulfillment.';
COMMENT ON COLUMN commerce.warehouses.priority IS
  'Lower value = higher priority when auto-selecting fulfillment warehouse.';
COMMENT ON COLUMN commerce.warehouses.metadata IS 'JSONB extension point.';
COMMENT ON COLUMN commerce.warehouses.external_id IS 'ERP warehouse identifier.';
COMMENT ON COLUMN commerce.warehouses.sync_status IS 'ERP sync state.';
COMMENT ON COLUMN commerce.warehouses.last_synced_at IS 'Last successful ERP sync.';
COMMENT ON COLUMN commerce.warehouses.created_at IS 'UTC row creation time.';
COMMENT ON COLUMN commerce.warehouses.updated_at IS 'UTC last update; trigger-maintained.';
COMMENT ON COLUMN commerce.warehouses.deleted_at IS 'Soft delete timestamp; NULL = active.';

CREATE UNIQUE INDEX warehouses_code_unique_active
  ON commerce.warehouses (code)
  WHERE deleted_at IS NULL;

COMMENT ON INDEX commerce.warehouses_code_unique_active IS
  'Warehouse codes unique among non-deleted rows.';

CREATE UNIQUE INDEX warehouses_one_default_active
  ON commerce.warehouses (is_default)
  WHERE is_default = TRUE AND deleted_at IS NULL;

COMMENT ON INDEX commerce.warehouses_one_default_active IS
  'At most one default warehouse among active rows.';

CREATE INDEX warehouses_is_active_idx
  ON commerce.warehouses (priority, name)
  WHERE is_active = TRUE AND deleted_at IS NULL;

COMMENT ON INDEX commerce.warehouses_is_active_idx IS
  'List active warehouses in fulfillment priority order.';

CREATE TRIGGER warehouses_set_updated_at
  BEFORE UPDATE ON commerce.warehouses
  FOR EACH ROW
  EXECUTE FUNCTION public.set_updated_at();

COMMENT ON TRIGGER warehouses_set_updated_at ON commerce.warehouses IS
  'Sets updated_at = NOW() on every UPDATE.';


-- =============================================================================
-- TABLE: commerce.inventory
-- Current stock balance per warehouse × product_variant (not product_id).
-- Mutations must always write a matching inventory_movements row (application).
-- =============================================================================
CREATE TABLE commerce.inventory (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  warehouse_id            UUID NOT NULL
    REFERENCES commerce.warehouses (id) ON DELETE RESTRICT,

  product_variant_id      UUID NOT NULL
    REFERENCES commerce.product_variants (id) ON DELETE RESTRICT,

  quantity_on_hand        INTEGER NOT NULL DEFAULT 0,
  quantity_reserved       INTEGER NOT NULL DEFAULT 0,
  low_stock_threshold     INTEGER,

  metadata                JSONB NOT NULL DEFAULT '{}'::jsonb,

  updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT inventory_warehouse_variant_unique UNIQUE (warehouse_id, product_variant_id),

  CONSTRAINT inventory_quantity_on_hand_nonneg CHECK (quantity_on_hand >= 0),

  CONSTRAINT inventory_quantity_reserved_nonneg CHECK (quantity_reserved >= 0),

  CONSTRAINT inventory_reserved_lte_on_hand CHECK (
    quantity_reserved <= quantity_on_hand
  ),

  CONSTRAINT inventory_low_stock_threshold_nonneg CHECK (
    low_stock_threshold IS NULL OR low_stock_threshold >= 0
  )
);

COMMENT ON TABLE commerce.inventory IS
  'Stock snapshot per warehouse and sellable SKU (product_variant). No soft delete.';

COMMENT ON COLUMN commerce.inventory.id IS 'UUID primary key.';
COMMENT ON COLUMN commerce.inventory.warehouse_id IS 'FK to commerce.warehouses.';
COMMENT ON COLUMN commerce.inventory.product_variant_id IS
  'FK to commerce.product_variants — inventory is always variant-scoped.';
COMMENT ON COLUMN commerce.inventory.quantity_on_hand IS
  'Physical units in warehouse (includes reserved).';
COMMENT ON COLUMN commerce.inventory.quantity_reserved IS
  'Units held by active stock_reservations (subset of on_hand).';
COMMENT ON COLUMN commerce.inventory.low_stock_threshold IS
  'Alert when (on_hand − reserved) falls at or below this value.';
COMMENT ON COLUMN commerce.inventory.metadata IS 'JSONB extension point.';
COMMENT ON COLUMN commerce.inventory.updated_at IS 'UTC last balance update; trigger-maintained.';

COMMENT ON CONSTRAINT inventory_reserved_lte_on_hand ON commerce.inventory IS
  'Reserved quantity cannot exceed on-hand quantity.';

CREATE INDEX inventory_product_variant_id_idx
  ON commerce.inventory (product_variant_id);

COMMENT ON INDEX commerce.inventory_product_variant_id_idx IS
  'Aggregate stock for a variant across warehouses.';

CREATE INDEX inventory_warehouse_id_idx
  ON commerce.inventory (warehouse_id);

COMMENT ON INDEX commerce.inventory_warehouse_id_idx IS
  'List all variant balances in a warehouse.';

CREATE TRIGGER inventory_set_updated_at
  BEFORE UPDATE ON commerce.inventory
  FOR EACH ROW
  EXECUTE FUNCTION public.set_updated_at();

COMMENT ON TRIGGER inventory_set_updated_at ON commerce.inventory IS
  'Sets updated_at = NOW() on every UPDATE.';


-- =============================================================================
-- VIEW: commerce.inventory_available
-- Computed sellable quantity (avoids GENERATED STORED column migration quirks).
-- =============================================================================
CREATE VIEW commerce.inventory_available AS
SELECT
  i.id,
  i.warehouse_id,
  i.product_variant_id,
  i.quantity_on_hand,
  i.quantity_reserved,
  (i.quantity_on_hand - i.quantity_reserved) AS quantity_available,
  i.low_stock_threshold,
  i.metadata,
  i.updated_at
FROM commerce.inventory AS i;

COMMENT ON VIEW commerce.inventory_available IS
  'Sellable stock per row: quantity_on_hand − quantity_reserved.';


-- =============================================================================
-- TABLE: commerce.inventory_movements
-- Append-only audit ledger for every stock change (adjustments, sales, transfers).
-- =============================================================================
CREATE TABLE commerce.inventory_movements (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  warehouse_id            UUID NOT NULL
    REFERENCES commerce.warehouses (id) ON DELETE RESTRICT,

  product_variant_id      UUID NOT NULL
    REFERENCES commerce.product_variants (id) ON DELETE RESTRICT,

  movement_type           TEXT NOT NULL,

  -- Signed change applied to quantity_on_hand (+ inbound, − outbound).
  quantity_delta          INTEGER NOT NULL,

  quantity_before         INTEGER NOT NULL,
  quantity_after          INTEGER NOT NULL,

  -- Polymorphic link to source document (order, transfer, manual adjustment, etc.).
  reference_type          TEXT,
  reference_id            UUID,

  reason                  TEXT,

  admin_id                UUID
    REFERENCES admin.admin_users (id) ON DELETE SET NULL,

  metadata                JSONB NOT NULL DEFAULT '{}'::jsonb,

  created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT inventory_movements_movement_type_check CHECK (
    movement_type IN (
      'purchase',
      'sale',
      'return',
      'adjustment',
      'transfer_in',
      'transfer_out',
      'reservation',
      'release'
    )
  ),

  CONSTRAINT inventory_movements_reference_type_check CHECK (
    reference_type IS NULL
    OR reference_type IN (
      'order',
      'cart',
      'return',
      'transfer',
      'reservation',
      'manual'
    )
  ),

  CONSTRAINT inventory_movements_quantity_after_consistent CHECK (
    quantity_after = quantity_before + quantity_delta
  ),

  CONSTRAINT inventory_movements_quantity_before_nonneg CHECK (
    quantity_before >= 0
  ),

  CONSTRAINT inventory_movements_quantity_after_nonneg CHECK (
    quantity_after >= 0
  )
);

COMMENT ON TABLE commerce.inventory_movements IS
  'Append-only stock movement audit log. Never UPDATE or DELETE rows.';

COMMENT ON COLUMN commerce.inventory_movements.id IS 'UUID primary key.';
COMMENT ON COLUMN commerce.inventory_movements.warehouse_id IS 'Warehouse where stock changed.';
COMMENT ON COLUMN commerce.inventory_movements.product_variant_id IS
  'Variant whose on_hand balance changed.';
COMMENT ON COLUMN commerce.inventory_movements.movement_type IS
  'purchase|sale|return|adjustment|transfer_in|transfer_out|reservation|release.';
COMMENT ON COLUMN commerce.inventory_movements.quantity_delta IS
  'Signed units added (+) or removed (−) from on_hand.';
COMMENT ON COLUMN commerce.inventory_movements.quantity_before IS 'on_hand immediately before change.';
COMMENT ON COLUMN commerce.inventory_movements.quantity_after IS 'on_hand immediately after change.';
COMMENT ON COLUMN commerce.inventory_movements.reference_type IS
  'Source document type: order, cart, return, transfer, reservation, manual.';
COMMENT ON COLUMN commerce.inventory_movements.reference_id IS
  'UUID of source row e.g. stock_transfers.id, stock_reservations.id.';
COMMENT ON COLUMN commerce.inventory_movements.reason IS 'Human-readable note for adjustments.';
COMMENT ON COLUMN commerce.inventory_movements.admin_id IS
  'Admin who performed manual adjustment or transfer.';
COMMENT ON COLUMN commerce.inventory_movements.metadata IS 'JSONB extension point.';
COMMENT ON COLUMN commerce.inventory_movements.created_at IS 'UTC when movement was recorded.';

CREATE INDEX inventory_movements_variant_created_idx
  ON commerce.inventory_movements (product_variant_id, created_at DESC);

COMMENT ON INDEX commerce.inventory_movements_variant_created_idx IS
  'Variant stock history timeline.';

CREATE INDEX inventory_movements_warehouse_id_idx
  ON commerce.inventory_movements (warehouse_id);

COMMENT ON INDEX commerce.inventory_movements_warehouse_id_idx IS
  'Warehouse-scoped movement queries.';

CREATE INDEX inventory_movements_reference_idx
  ON commerce.inventory_movements (reference_type, reference_id)
  WHERE reference_type IS NOT NULL AND reference_id IS NOT NULL;

COMMENT ON INDEX commerce.inventory_movements_reference_idx IS
  'Lookup movements by source document.';

CREATE INDEX inventory_movements_movement_type_idx
  ON commerce.inventory_movements (movement_type);

COMMENT ON INDEX commerce.inventory_movements_movement_type_idx IS
  'Filter movements by type e.g. adjustment reports.';

CREATE INDEX inventory_movements_created_at_idx
  ON commerce.inventory_movements (created_at DESC);

COMMENT ON INDEX commerce.inventory_movements_created_at_idx IS
  'Recent movements across all variants.';

CREATE OR REPLACE FUNCTION commerce.inventory_movements_prevent_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION
    'commerce.inventory_movements is append-only; UPDATE and DELETE are forbidden';
END;
$$;

COMMENT ON FUNCTION commerce.inventory_movements_prevent_mutation() IS
  'Blocks UPDATE/DELETE on inventory_movements for audit integrity.';

CREATE TRIGGER inventory_movements_prevent_update
  BEFORE UPDATE ON commerce.inventory_movements
  FOR EACH ROW
  EXECUTE FUNCTION commerce.inventory_movements_prevent_mutation();

CREATE TRIGGER inventory_movements_prevent_delete
  BEFORE DELETE ON commerce.inventory_movements
  FOR EACH ROW
  EXECUTE FUNCTION commerce.inventory_movements_prevent_mutation();

COMMENT ON TRIGGER inventory_movements_prevent_update ON commerce.inventory_movements IS
  'Enforces append-only audit ledger.';
COMMENT ON TRIGGER inventory_movements_prevent_delete ON commerce.inventory_movements IS
  'Enforces append-only audit ledger.';


-- =============================================================================
-- TABLE: commerce.stock_reservations
-- Temporary holds on variant stock (cart TTL or order checkout).
-- order_id / cart_id FKs added in migration 000016.
-- =============================================================================
CREATE TABLE commerce.stock_reservations (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  warehouse_id            UUID NOT NULL
    REFERENCES commerce.warehouses (id) ON DELETE RESTRICT,

  product_variant_id      UUID NOT NULL
    REFERENCES commerce.product_variants (id) ON DELETE RESTRICT,

  -- Placeholder UUIDs until commerce.orders / commerce.carts exist (000016).
  order_id                UUID,
  cart_id                 UUID,

  quantity                INTEGER NOT NULL,

  status                  TEXT NOT NULL DEFAULT 'active',

  expires_at              TIMESTAMPTZ,

  metadata                JSONB NOT NULL DEFAULT '{}'::jsonb,

  created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT stock_reservations_quantity_positive CHECK (quantity > 0),

  CONSTRAINT stock_reservations_status_check CHECK (
    status IN ('active', 'committed', 'released', 'expired')
  )
);

COMMENT ON TABLE commerce.stock_reservations IS
  'Inventory holds on product_variant stock; ties to cart or order when set.';

COMMENT ON COLUMN commerce.stock_reservations.id IS 'UUID primary key.';
COMMENT ON COLUMN commerce.stock_reservations.warehouse_id IS
  'Warehouse from which stock is reserved.';
COMMENT ON COLUMN commerce.stock_reservations.product_variant_id IS
  'Variant SKU being held.';
COMMENT ON COLUMN commerce.stock_reservations.order_id IS
  'Set when reservation is tied to an order; FK added in 000016.';
COMMENT ON COLUMN commerce.stock_reservations.cart_id IS
  'Set for guest/user cart holds; FK added in 000016.';
COMMENT ON COLUMN commerce.stock_reservations.quantity IS 'Units reserved (> 0).';
COMMENT ON COLUMN commerce.stock_reservations.status IS
  'active=holding stock; committed=converted to order; released|expired=cancelled.';
COMMENT ON COLUMN commerce.stock_reservations.expires_at IS
  'TTL for cart reservations; NULL = no automatic expiry.';
COMMENT ON COLUMN commerce.stock_reservations.metadata IS 'JSONB extension point.';
COMMENT ON COLUMN commerce.stock_reservations.created_at IS 'UTC when hold was created.';
COMMENT ON COLUMN commerce.stock_reservations.updated_at IS 'UTC last status change.';

CREATE INDEX stock_reservations_variant_active_idx
  ON commerce.stock_reservations (product_variant_id, status)
  WHERE status = 'active';

COMMENT ON INDEX commerce.stock_reservations_variant_active_idx IS
  'Sum active reservations per variant for availability checks.';

CREATE INDEX stock_reservations_expires_at_idx
  ON commerce.stock_reservations (expires_at)
  WHERE status = 'active' AND expires_at IS NOT NULL;

COMMENT ON INDEX commerce.stock_reservations_expires_at_idx IS
  'Background job to expire stale cart reservations.';

CREATE INDEX stock_reservations_order_id_idx
  ON commerce.stock_reservations (order_id)
  WHERE order_id IS NOT NULL;

COMMENT ON INDEX commerce.stock_reservations_order_id_idx IS
  'Lookup reservations for an order (FK in 000016).';

CREATE INDEX stock_reservations_cart_id_idx
  ON commerce.stock_reservations (cart_id)
  WHERE cart_id IS NOT NULL;

COMMENT ON INDEX commerce.stock_reservations_cart_id_idx IS
  'Lookup reservations for a cart (FK in 000016).';

CREATE INDEX stock_reservations_warehouse_id_idx
  ON commerce.stock_reservations (warehouse_id);

COMMENT ON INDEX commerce.stock_reservations_warehouse_id_idx IS
  'Warehouse-scoped reservation queries.';

CREATE TRIGGER stock_reservations_set_updated_at
  BEFORE UPDATE ON commerce.stock_reservations
  FOR EACH ROW
  EXECUTE FUNCTION public.set_updated_at();

COMMENT ON TRIGGER stock_reservations_set_updated_at ON commerce.stock_reservations IS
  'Sets updated_at = NOW() on every UPDATE.';


-- =============================================================================
-- TABLE: commerce.stock_transfers
-- Inter-warehouse transfer document header. Line detail via inventory_movements
-- (movement_type transfer_out / transfer_in, reference_type transfer).
-- =============================================================================
CREATE TABLE commerce.stock_transfers (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  from_warehouse_id       UUID NOT NULL
    REFERENCES commerce.warehouses (id) ON DELETE RESTRICT,

  to_warehouse_id         UUID NOT NULL
    REFERENCES commerce.warehouses (id) ON DELETE RESTRICT,

  status                  TEXT NOT NULL DEFAULT 'draft',

  admin_id                UUID NOT NULL
    REFERENCES admin.admin_users (id) ON DELETE RESTRICT,

  notes                   TEXT,

  metadata                JSONB NOT NULL DEFAULT '{}'::jsonb,

  created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  completed_at            TIMESTAMPTZ,

  CONSTRAINT stock_transfers_status_check CHECK (
    status IN ('draft', 'in_transit', 'completed', 'cancelled')
  ),

  CONSTRAINT stock_transfers_distinct_warehouses CHECK (
    from_warehouse_id <> to_warehouse_id
  )
);

COMMENT ON TABLE commerce.stock_transfers IS
  'Warehouse-to-warehouse transfer document; movements record variant quantities.';

COMMENT ON COLUMN commerce.stock_transfers.id IS 'UUID primary key.';
COMMENT ON COLUMN commerce.stock_transfers.from_warehouse_id IS 'Source warehouse.';
COMMENT ON COLUMN commerce.stock_transfers.to_warehouse_id IS 'Destination warehouse.';
COMMENT ON COLUMN commerce.stock_transfers.status IS
  'draft → in_transit → completed, or cancelled.';
COMMENT ON COLUMN commerce.stock_transfers.admin_id IS 'Admin who initiated the transfer.';
COMMENT ON COLUMN commerce.stock_transfers.notes IS 'Optional transfer notes.';
COMMENT ON COLUMN commerce.stock_transfers.metadata IS 'JSONB extension point.';
COMMENT ON COLUMN commerce.stock_transfers.created_at IS 'UTC document creation.';
COMMENT ON COLUMN commerce.stock_transfers.updated_at IS 'UTC last status change.';
COMMENT ON COLUMN commerce.stock_transfers.completed_at IS
  'UTC when transfer reached completed status.';

CREATE INDEX stock_transfers_from_warehouse_id_idx
  ON commerce.stock_transfers (from_warehouse_id);

COMMENT ON INDEX commerce.stock_transfers_from_warehouse_id_idx IS
  'Outbound transfers from a warehouse.';

CREATE INDEX stock_transfers_to_warehouse_id_idx
  ON commerce.stock_transfers (to_warehouse_id);

COMMENT ON INDEX commerce.stock_transfers_to_warehouse_id_idx IS
  'Inbound transfers to a warehouse.';

CREATE INDEX stock_transfers_status_idx
  ON commerce.stock_transfers (status)
  WHERE status IN ('draft', 'in_transit');

COMMENT ON INDEX commerce.stock_transfers_status_idx IS
  'Open transfers awaiting completion.';

CREATE TRIGGER stock_transfers_set_updated_at
  BEFORE UPDATE ON commerce.stock_transfers
  FOR EACH ROW
  EXECUTE FUNCTION public.set_updated_at();

COMMENT ON TRIGGER stock_transfers_set_updated_at ON commerce.stock_transfers IS
  'Sets updated_at = NOW() on every UPDATE.';


-- =============================================================================
-- TABLE: commerce.fulfillment_allocations
-- Which warehouse fulfills each order line. order_item_id FK deferred to 000016.
-- =============================================================================
CREATE TABLE commerce.fulfillment_allocations (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  -- Placeholder until commerce.order_items exists (000016).
  order_item_id           UUID,

  warehouse_id            UUID NOT NULL
    REFERENCES commerce.warehouses (id) ON DELETE RESTRICT,

  product_variant_id      UUID NOT NULL
    REFERENCES commerce.product_variants (id) ON DELETE RESTRICT,

  quantity                INTEGER NOT NULL,

  status                  TEXT NOT NULL DEFAULT 'allocated',

  metadata                JSONB NOT NULL DEFAULT '{}'::jsonb,

  created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT fulfillment_allocations_quantity_positive CHECK (quantity > 0),

  CONSTRAINT fulfillment_allocations_status_check CHECK (
    status IN ('allocated', 'picked', 'shipped', 'cancelled')
  )
);

COMMENT ON TABLE commerce.fulfillment_allocations IS
  'Warehouse allocation per order line item; variant denormalised for inventory joins.';

COMMENT ON COLUMN commerce.fulfillment_allocations.id IS 'UUID primary key.';
COMMENT ON COLUMN commerce.fulfillment_allocations.order_item_id IS
  'FK to commerce.order_items; constraint added in 000016.';
COMMENT ON COLUMN commerce.fulfillment_allocations.warehouse_id IS
  'Warehouse fulfilling this portion of the line.';
COMMENT ON COLUMN commerce.fulfillment_allocations.product_variant_id IS
  'Sellable SKU being picked (matches order_items.variant).';
COMMENT ON COLUMN commerce.fulfillment_allocations.quantity IS 'Units allocated (> 0).';
COMMENT ON COLUMN commerce.fulfillment_allocations.status IS
  'allocated → picked → shipped, or cancelled.';
COMMENT ON COLUMN commerce.fulfillment_allocations.metadata IS 'JSONB extension point.';
COMMENT ON COLUMN commerce.fulfillment_allocations.created_at IS 'UTC allocation created.';
COMMENT ON COLUMN commerce.fulfillment_allocations.updated_at IS 'UTC last status change.';

CREATE INDEX fulfillment_allocations_order_item_id_idx
  ON commerce.fulfillment_allocations (order_item_id)
  WHERE order_item_id IS NOT NULL;

COMMENT ON INDEX commerce.fulfillment_allocations_order_item_id_idx IS
  'All allocations for an order line (FK in 000016).';

CREATE INDEX fulfillment_allocations_warehouse_status_idx
  ON commerce.fulfillment_allocations (warehouse_id, status)
  WHERE status IN ('allocated', 'picked');

COMMENT ON INDEX commerce.fulfillment_allocations_warehouse_status_idx IS
  'Pick-list queries for a warehouse.';

CREATE INDEX fulfillment_allocations_variant_id_idx
  ON commerce.fulfillment_allocations (product_variant_id);

COMMENT ON INDEX commerce.fulfillment_allocations_variant_id_idx IS
  'Variant-scoped fulfillment history.';

CREATE TRIGGER fulfillment_allocations_set_updated_at
  BEFORE UPDATE ON commerce.fulfillment_allocations
  FOR EACH ROW
  EXECUTE FUNCTION public.set_updated_at();

COMMENT ON TRIGGER fulfillment_allocations_set_updated_at ON commerce.fulfillment_allocations IS
  'Sets updated_at = NOW() on every UPDATE.';


-- =============================================================================
-- VERIFICATION QUERIES (run manually after migrate.py — do not execute here)
-- =============================================================================
--
-- 1. All six tables exist:
--    SELECT table_name FROM information_schema.tables
--    WHERE table_schema = 'commerce'
--      AND table_name IN (
--        'warehouses', 'inventory', 'inventory_movements',
--        'stock_reservations', 'stock_transfers', 'fulfillment_allocations'
--      )
--    ORDER BY table_name;
--    -- Expect: 6 rows
--
-- 2. inventory is keyed by product_variant_id (not product_id):
--    SELECT column_name, data_type
--    FROM information_schema.columns
--    WHERE table_schema = 'commerce' AND table_name = 'inventory'
--      AND column_name IN ('product_variant_id', 'product_id');
--    -- Expect: product_variant_id uuid only; no product_id
--
-- 3. UNIQUE (warehouse_id, product_variant_id) on inventory:
--    SELECT indexname, indexdef FROM pg_indexes
--    WHERE schemaname = 'commerce' AND tablename = 'inventory'
--      AND indexdef ILIKE '%warehouse_id%product_variant_id%';
--
-- 4. inventory_movements is append-only (triggers exist):
--    SELECT tgname FROM pg_trigger t
--    JOIN pg_class c ON c.oid = t.tgrelid
--    JOIN pg_namespace n ON n.oid = c.relnamespace
--    WHERE n.nspname = 'commerce' AND c.relname = 'inventory_movements'
--      AND NOT t.tgisinternal;
--    -- Expect: inventory_movements_prevent_update, inventory_movements_prevent_delete
--
-- 5. Deferred FK placeholders (no FK on order_id yet):
--    SELECT conname, confrelid::regclass
--    FROM pg_constraint
--    WHERE conrelid = 'commerce.stock_reservations'::regclass
--      AND contype = 'f';
--    -- Expect: warehouse_id, product_variant_id only — not orders/carts
--
-- 6. inventory_available view:
--    SELECT table_name FROM information_schema.views
--    WHERE table_schema = 'commerce' AND table_name = 'inventory_available';
--
-- 7. Smoke test (requires warehouse + variant; BEGIN; ROLLBACK):
--    INSERT INTO commerce.warehouses (code, name)
--      VALUES ('WH-TEST-01', 'Test Warehouse') RETURNING id;
--    -- use variant id from commerce.product_variants
--    INSERT INTO commerce.inventory (warehouse_id, product_variant_id, quantity_on_hand)
--      VALUES ('<warehouse_uuid>', '<variant_uuid>', 100) RETURNING id;
--    INSERT INTO commerce.inventory_movements (
--      warehouse_id, product_variant_id, movement_type,
--      quantity_delta, quantity_before, quantity_after,
--      reference_type, reason, admin_id
--    ) VALUES (
--      '<warehouse_uuid>', '<variant_uuid>', 'adjustment',
--      10, 100, 110, 'manual', 'opening adjustment', NULL
--    );
--    SELECT quantity_available FROM commerce.inventory_available
--      WHERE warehouse_id = '<warehouse_uuid>'
--        AND product_variant_id = '<variant_uuid>';
--    -- Expect: 110 (no reservations)
--
-- 8. Append-only enforcement:
--    -- UPDATE commerce.inventory_movements SET reason = 'x' WHERE false;
--    -- Expect: ERROR append-only
--
-- 9. Migration recorded:
--    SELECT version FROM public.schema_migrations
--    WHERE version = '000014_commerce_inventory';


-- =============================================================================
-- ROLLBACK SQL (manual only — reverse order of creation)
-- =============================================================================
--
-- DROP VIEW IF EXISTS commerce.inventory_available;
--
-- DROP TRIGGER IF EXISTS fulfillment_allocations_set_updated_at
--   ON commerce.fulfillment_allocations;
-- DROP TABLE IF EXISTS commerce.fulfillment_allocations;
--
-- DROP TRIGGER IF EXISTS stock_transfers_set_updated_at ON commerce.stock_transfers;
-- DROP TABLE IF EXISTS commerce.stock_transfers;
--
-- DROP TRIGGER IF EXISTS stock_reservations_set_updated_at ON commerce.stock_reservations;
-- DROP TABLE IF EXISTS commerce.stock_reservations;
--
-- DROP TRIGGER IF EXISTS inventory_movements_prevent_delete ON commerce.inventory_movements;
-- DROP TRIGGER IF EXISTS inventory_movements_prevent_update ON commerce.inventory_movements;
-- DROP FUNCTION IF EXISTS commerce.inventory_movements_prevent_mutation();
-- DROP TABLE IF EXISTS commerce.inventory_movements;
--
-- DROP TRIGGER IF EXISTS inventory_set_updated_at ON commerce.inventory;
-- DROP TABLE IF EXISTS commerce.inventory;
--
-- DROP TRIGGER IF EXISTS warehouses_set_updated_at ON commerce.warehouses;
-- DROP TABLE IF EXISTS commerce.warehouses;
--
-- DELETE FROM public.schema_migrations
-- WHERE version = '000014_commerce_inventory';
--
-- -- Verify:
-- -- SELECT table_name FROM information_schema.tables
-- -- WHERE table_schema = 'commerce'
-- --   AND table_name IN (
-- --     'warehouses', 'inventory', 'inventory_movements',
-- --     'stock_reservations', 'stock_transfers', 'fulfillment_allocations'
-- --   );
-- -- Expect: 0 rows
