# Chic A Boo — Migration Blueprint (v3.0)

**Status:** Approved roadmap — SQL not authored yet  
**Baseline:** Migrations `000001`–`000010` applied on Supabase  
**Target:** Architecture v3.0 (`ARCHITECTURE_v3.md`)  
**Audience:** Backend team authoring migrations  

---

## 1. Executive summary

Phase 1 adds **16 migrations** (`000011`–`000026`) introducing:

- 2 new schemas: `commerce`, `ops`
- 35 new tables
- 3 altered existing tables (`public.user_preferences`, `admin.audit_logs`, `identity.email_otps` → rename)
- 1 table relocation: `public.payment_customers` → `commerce.payment_customers`
- 1 new identity table: `identity.consent_records`
- Deferred search GIN indexes and RLS v3 policies until tables exist
- Reference seed data in final migration only

**Estimated net new tables after Phase 1:** ~38 commerce/ops + existing 13 identity/public/admin ≈ **51 total**

---

## 2. Principles for authoring SQL

| Rule | Rationale |
|------|-----------|
| One migration = one deployable unit | Rollback story per file |
| CREATE TABLE + FK + CHECK in same migration | Avoid orphan tables |
| B-tree / UNIQUE indexes **with** table creation | FK performance from day one |
| GIN / pg_trgm indexes **deferred** to `000024` | Faster bulk seed; smaller lock windows |
| RLS **deferred** to `000025` | Policies reference final table names/schemas |
| Seeds **only** in `000026` | Idempotent `ON CONFLICT DO NOTHING` |
| Money columns = `BIGINT` paise | v3.0 ADR-002 |
| No `deleted_at` on financial tables | orders, order_items, payments, payment_transactions |
| Migrations run on **session pooler** port 5432 | Supabase direct IPv6 may fail |
| Each migration wrapped in transaction | `migrate.py` already commits per file |

---

## 3. Dependency graph

```
Phase 0 (done)
  000001 extensions + identity/admin schemas
  000002–000004 identity, public, admin tables
  000005 triggers
  000006 RLS v1
  000007 RBAC seed
  000008–000009 v1.1 enhancements + RLS additions
  000010 auth→identity rename guard

Phase 1
  000011 create commerce + ops schemas
        │
        ├─► 000012 media_assets, categories
        │         │
        │         └─► 000013 products → variants → images, tags
        │                   │
        │                   └─► 000014 warehouses → stock → movements
        │                             │              reservations
        │                             │              transfers, fulfillment_allocations
        │                             │
        ├─► 000015 carts, cart_items, wishlist_items (needs variants from 013)
        │
        ├─► 000016 orders → order_items → tax_lines → status_history → invoices
        │         │
        │         ├─► 000017 payment_customers (move) → payments → transactions → refunds
        │         │
        │         ├─► 000018 coupons → shipments → shipment_items → tracking
        │         │
        │         └─► 000019 returns → reviews
        │
        ├─► 000020 ops tables (idempotency, webhooks, notification_logs)
        │         └── FK to payments/orders optional nullable — can run after 017
        │
        ├─► 000021 identity consent + email_verifications rename
        ├─► 000022 user_preferences columns
        ├─► 000023 admin audit_logs columns
        │
        ├─► 000024 search triggers + GIN indexes (needs 012, 013)
        ├─► 000025 RLS v3 (needs all tables + payment_customers move)
        └─► 000026 seed reference data (needs 012, 014)
```

**Critical path:** `011 → 012 → 013 → 014 → 016 → 017 → 025 → 026`

**Parallel-safe after 013:** `015`, `020`, `021`, `022`, `023` (no cross-deps) — still ship sequentially by number for linear history.

---

## 4. Phase 0 baseline (already applied)

Do **not** re-run. Documented for dependency context.

| Migration | Schemas | Tables / objects |
|-----------|---------|------------------|
| `000001` | `identity`, `admin` | extensions: `pg_trgm`, `uuid-ossp`, `pgcrypto` |
| `000002` | `identity` | `users`, `refresh_tokens`, `email_otps`, `password_resets`, `security_logs` |
| `000003` | `public` | `user_profiles`, `user_addresses`, `user_preferences` |
| `000004` | `admin` | `roles`, `permissions`, `role_permissions`, `admin_users`, `audit_logs` |
| `000005` | — | `set_updated_at()`, `current_user_id()`, triggers |
| `000006` | — | RLS v1 on `identity.users`, `public.user_*` |
| `000007` | — | RBAC roles + permissions seed |
| `000008` | `identity`, `public` | user security cols; `user_devices`, `login_history`, `payment_customers`, `admin_sessions`; address cols |
| `000009` | — | permissions v2; RLS on `user_devices`, `login_history`, `payment_customers` |
| `000010` | — | rename legacy `auth` schema guard (no-op on Supabase) |

**Live objects requiring migration handling in Phase 1:**

| Object | Action in Phase 1 |
|--------|-------------------|
| `public.payment_customers` | Relocate to `commerce.payment_customers` in `000017` |
| `identity.email_otps` | Rename → `email_verifications` + columns in `000021` |
| RLS on `public.payment_customers` | Drop in `000017`; recreate on `commerce.payment_customers` in `000025` |

---

## 5. Phase 1 migration catalogue

### `000011_create_commerce_and_ops_schemas`

| Attribute | Detail |
|-----------|--------|
| **Depends on** | `000010` |
| **Blocks** | All `000012`–`000026` |

**Creates:**
- Schema `commerce` + comment
- Schema `ops` + comment
- Sequences (empty shells only):

| Sequence | Start | Purpose |
|----------|-------|---------|
| `commerce.order_number_seq` | 1000001 | `orders.order_number` |
| `commerce.invoice_number_seq` | 100001 | `invoices.invoice_number` |
| `commerce.return_number_seq` | 500001 | `returns.return_number` |
| `commerce.shipment_number_seq` | 700001 | `shipments.shipment_number` |

**Tables:** none  
**Indexes:** none  
**RLS:** none  
**Seed:** none  
**Backfill:** none  

---

### `000012_commerce_media_and_categories`

| Attribute | Detail |
|-----------|--------|
| **Depends on** | `000011` |
| **Blocks** | `000013`, `000024`, `000026` |

**Tables:**

| Table | Soft delete | Notes |
|-------|-------------|-------|
| `commerce.media_assets` | No | R2 registry; `r2_key` UNIQUE |
| `commerce.categories` | Yes | `parent_id` self-FK; `path`, `depth` nullable until trigger in 024 |

**Constraints:** CHECK `categories.status IN ('active','inactive')`

**Indexes (create inline — P0/P1):**

| Table | Index |
|-------|-------|
| `media_assets` | UNIQUE `r2_key` |
| `categories` | UNIQUE partial `slug` WHERE `deleted_at IS NULL` |
| `categories` | `parent_id` |
| `categories` | `path` |
| `categories` | partial `status` WHERE active |

**Deferred to 000024:** GIN `search_vector`, GIN trgm `name`

**Triggers:** `categories_set_updated_at` only (path/search in 024)

**RLS:** none  
**Seed:** none  

---

### `000013_commerce_products`

| Attribute | Detail |
|-----------|--------|
| **Depends on** | `000012` |
| **Blocks** | `000014`, `000015`, `000016`, `000018`, `000019`, `000024` |

**Tables (creation order within migration):**

1. `commerce.products` — FK `primary_category_id` → `categories`
2. `commerce.product_categories` — FK products, categories
3. `commerce.product_variants` — FK products
4. `commerce.product_images` — FK products, variants, `media_assets`
5. `commerce.product_tags`
6. `commerce.product_tag_mappings` — FK products, tags

**Constraints:**
- `products.status` CHECK: `draft`, `active`, `inactive`
- `products.tax_class` CHECK: `gst_5`, `gst_12`, `gst_18`, `gst_28`, `exempt`
- `product_variants.price_paise` CHECK `>= 0`
- UNIQUE partial: `products.slug`, `variants.sku`, `variants.barcode`
- UNIQUE: `product_categories (product_id, category_id)`
- UNIQUE: `product_tag_mappings (product_id, tag_id)`
- UNIQUE partial: one `is_primary` image per product

**Indexes (inline):**

| Table | Indexes |
|-------|---------|
| `products` | `primary_category_id`, partial `status`, partial `is_featured`, `vendor_id` partial |
| `product_categories` | `category_id`, `product_id` |
| `product_variants` | `product_id` |
| `product_images` | `product_id`, `(product_id, sort_order)` |
| `product_tags` | UNIQUE `slug`, UNIQUE `name` |
| `product_tag_mappings` | `tag_id` |

**Deferred to 000024:** `products.search_vector` GIN, trgm `products.name`, trgm `variants.sku`

**Triggers:** `updated_at` on products, variants

**RLS:** none (catalog reads via service role)

**Seed:** none  

---

### `000014_commerce_inventory`

| Attribute | Detail |
|-----------|--------|
| **Depends on** | `000013` (`product_variants`) |
| **Blocks** | `000016`, `000018`, `000026` |

**Tables (order):**

1. `commerce.warehouses`
2. `commerce.inventory_stock` — FK warehouse, variant
3. `commerce.inventory_movements` — append-only
4. `commerce.stock_reservations` — FK warehouse, variant; nullable FK placeholders for `cart_id`, `order_id` (orders table not yet exist — add FK in `000016` or use nullable UUID without FK until 016)

**Design note:** Create `stock_reservations.order_id` and `cart_id` as **nullable UUID without FK** in `000014`, then `ALTER TABLE ADD CONSTRAINT` in `000016` after orders/carts exist.

5. `commerce.stock_transfers` — FK warehouses
6. `commerce.fulfillment_allocations` — FK `order_item_id` deferred to `000016`

**Constraints:**
- `inventory_stock`: CHECK quantities ≥ 0, reserved ≤ on_hand
- UNIQUE `(warehouse_id, variant_id)`
- `movements.movement_type` CHECK enumerated list
- `reservations.status` CHECK: `active`, `committed`, `released`, `expired`

**Indexes (inline):**

| Table | Indexes |
|-------|---------|
| `warehouses` | UNIQUE `code` partial |
| `inventory_stock` | UNIQUE `(warehouse_id, variant_id)`, `variant_id` |
| `inventory_movements` | `(variant_id, created_at DESC)`, `warehouse_id`, `(reference_type, reference_id)` |
| `stock_reservations` | partial `(variant_id, status)`, `expires_at`, `cart_id`, `order_id` |
| `stock_transfers` | `from_warehouse_id`, `to_warehouse_id`, `status` |

**Deferred P3:** partial low-stock index (add in `000024` or post-seed)

**RLS:** none  

**Seed:** none (warehouse seeded in `000026`)

---

### `000015_commerce_cart_and_wishlist`

| Attribute | Detail |
|-----------|--------|
| **Depends on** | `000013` (variants) |
| **Blocks** | `000016`, `000025` |

**Tables:**

1. `commerce.carts` — FK `user_id` → `identity.users`; nullable `coupon_id` (FK added in `000018`)
2. `commerce.cart_items` — FK cart, variant
3. `commerce.wishlist_items` — FK `user_id`, `product_id`, optional `variant_id`

**Constraints:**
- `carts`: CHECK `user_id IS NOT NULL OR guest_token IS NOT NULL`
- `carts.status` CHECK: `active`, `converted`, `abandoned`, `expired`
- UNIQUE partial `guest_token` WHERE active
- UNIQUE partial `(cart_id, variant_id)` on cart_items WHERE not deleted
- UNIQUE `(user_id, product_id, variant_id)` on wishlist_items

**Indexes (inline):** `carts.user_id` partial, `carts.guest_token` partial, `carts.expires_at`, `cart_items.cart_id`, `wishlist_items.user_id`

**RLS:** policies in `000025`

**Seed:** none  

---

### `000016_commerce_orders`

| Attribute | Detail |
|-----------|--------|
| **Depends on** | `000013`, `000014`, `000015` |
| **Blocks** | `000017`, `000018`, `000019`, `000020` |

**Tables:**

1. `commerce.orders` — **no `deleted_at`**
2. `commerce.order_items` — FK order, variant, product
3. `commerce.order_tax_lines`
4. `commerce.order_status_history` — append-only
5. `commerce.invoices` — FK order UNIQUE

**Also in this migration:**
- ADD FK `stock_reservations.order_id` → `orders`
- ADD FK `stock_reservations.cart_id` → `carts`
- ADD FK `fulfillment_allocations.order_item_id` → `order_items`

**Constraints:**
- All money columns BIGINT paise
- `orders.status` lifecycle CHECK
- `orders.payment_status`, `fulfillment_status` CHECK lists
- `order_items.quantity` CHECK > 0
- `order_tax_lines.tax_type` CHECK: `cgst`, `sgst`, `igst`, `cess`

**Indexes (inline):**

| Table | Indexes |
|-------|---------|
| `orders` | UNIQUE `order_number`, `(user_id, created_at DESC)`, `status`, `payment_status`, partial `guest_email` |
| `order_items` | `order_id`, `variant_id`, `product_id` |
| `order_tax_lines` | `order_id` |
| `order_status_history` | `(order_id, created_at DESC)` |
| `invoices` | UNIQUE `order_id`, UNIQUE `invoice_number` |

**RLS:** `000025`

**Seed:** none  

---

### `000017_commerce_payments`

| Attribute | Detail |
|-----------|--------|
| **Depends on** | `000016` |
| **Blocks** | `000020`, `000025` |

**Tables:**

1. `commerce.payments` — multiple per `order_id`; UNIQUE `(order_id, attempt_number)`
2. `commerce.payment_transactions` — append-only
3. `commerce.refunds`

**Relocation: `public.payment_customers` → `commerce.payment_customers`**

See §8 Backfill strategy.

**Constraints:**
- `payments.provider` CHECK includes `razorpay`
- UNIQUE partial `provider_payment_id`
- `payment_transactions.transaction_type` CHECK
- `refunds.status` CHECK

**Indexes (inline):** `payments.order_id`, `payments.provider_payment_id` partial unique, `payment_transactions.payment_id`, `refunds.order_id`, `refunds.payment_id`

**RLS:** drop old `public.payment_customers` policies; new policy in `000025`

**Seed:** none  

---

### `000018_commerce_coupons_and_shipping`

| Attribute | Detail |
|-----------|--------|
| **Depends on** | `000016`, `000014` (warehouses) |
| **Blocks** | `000019`, `000025` |

**Tables:**

1. `commerce.coupons` — **no `usage_count` column**
2. `commerce.coupon_usages`
3. `commerce.shipments`
4. `commerce.shipment_items`
5. `commerce.shipment_tracking_events` — append-only

**Also:** ADD FK `carts.coupon_id` → `coupons`

**Constraints:**
- `coupons.discount_type` CHECK: `percentage`, `fixed_amount`, `free_shipping`
- `coupons.applies_to` CHECK: `all`, `category`, `product`, `customer`
- UNIQUE partial `code_normalized`
- `shipment_items.quantity` CHECK > 0

**Indexes (inline):** coupon normalized code, `coupon_usages (user_id, coupon_id)`, `shipments.order_id`, `shipment_items.shipment_id`, tracking `(shipment_id, event_at DESC)`

**RLS:** none on coupons/shipments (service role)

**Seed:** none (optional sample coupon in 026 — see §6)

---

### `000019_commerce_returns_and_reviews`

| Attribute | Detail |
|-----------|--------|
| **Depends on** | `000016`, `000013` |
| **Blocks** | `000025` |

**Tables:**

1. `commerce.returns`
2. `commerce.return_items`
3. `commerce.reviews`
4. `commerce.review_images` — FK `media_assets`
5. `commerce.review_votes`

**Constraints:**
- `returns.status` CHECK lifecycle
- `reviews.rating` CHECK 1–5
- `reviews.status` CHECK: `pending`, `approved`, `rejected`, `hidden`
- UNIQUE partial `(user_id, product_id, order_id)` on reviews
- UNIQUE `(review_id, user_id)` on review_votes

**Indexes (inline):** `returns.order_id`, `reviews.product_id` partial approved, `review_votes.review_id`

**RLS:** reviews + returns in `000025`

**Seed:** none  

---

### `000020_ops_infrastructure`

| Attribute | Detail |
|-----------|--------|
| **Depends on** | `000011`; soft dep `000017` for nullable FKs on webhook_events |
| **Blocks** | `000025` |

**Tables:**

1. `ops.idempotency_keys` — PK `key` TEXT
2. `ops.webhook_events` — nullable FK `payment_id`, `order_id`
3. `ops.notification_logs` — no FK (reference_type/id pattern)

**Constraints:**
- UNIQUE `(provider, provider_event_id)` on webhook_events
- `webhook_events.status` CHECK

**Indexes (inline):** `idempotency_keys.created_at`, `webhook_events.status` partial failed, `webhook_events.created_at`, `notification_logs (reference_type, reference_id)`

**RLS:** none (service only)

**Seed:** none  

---

### `000021_identity_consent_and_email_renames`

| Attribute | Detail |
|-----------|--------|
| **Depends on** | `000002` (email_otps exists) |
| **Blocks** | none critical |

**Creates:**
- `identity.consent_records` — append-only consent history

**Alters / renames:**
- `identity.email_otps` → `identity.email_verifications`
- ADD columns: `email_normalized`, `purpose`, `max_attempts`, `verified_at`

See §8 Backfill for email_verifications.

**Indexes:** `consent_records (user_id, created_at DESC)`, `email_verifications (email_normalized, purpose)` partial unverified

**RLS:** none on consent (service writes); existing identity RLS unchanged

**Seed:** none  

---

### `000022_extend_user_preferences`

| Attribute | Detail |
|-----------|--------|
| **Depends on** | `000003` |
| **Blocks** | none |

**Alters `public.user_preferences` ADD columns:**

| Column | Default |
|--------|---------|
| `email_marketing` | FALSE |
| `sms_marketing` | FALSE |
| `push_notifications` | TRUE |
| `order_updates_email` | TRUE |
| `order_updates_sms` | FALSE |

**Backfill:** defaults apply to existing rows via DEFAULT

**Seed:** none  

---

### `000023_extend_admin_audit_logs`

| Attribute | Detail |
|-----------|--------|
| **Depends on** | `000004`, `000008` |
| **Blocks** | none |

**Alters `admin.audit_logs` ADD columns:**

| Column | Nullable |
|--------|----------|
| `domain` | YES |
| `actor_type` | YES |
| `service_name` | YES |
| `correlation_id` | YES |

**Backfill:** existing rows: `actor_type = 'admin'`, `domain` inferred NULL OK, `service_name = 'admin'`

**Indexes:** `audit_logs (domain, created_at DESC)` — add inline

**Seed:** none  

---

### `000024_search_triggers_and_gin_indexes`

| Attribute | Detail |
|-----------|--------|
| **Depends on** | `000012`, `000013` |
| **Blocks** | none (run before RLS ok) |

**Functions & triggers:**

| Object | Purpose |
|--------|---------|
| `commerce.refresh_category_path()` | maintain `path`, `depth` on category insert/update |
| `commerce.refresh_product_search_vector()` | rebuild `search_vector` from name, brand, description |
| Triggers on `categories`, `products` | call above |
| Trigger on `product_tag_mappings` | re-index parent product search_vector |

**Column adds (if not created in 012/013):**
- `categories.search_vector TSVECTOR`
- `products.search_vector TSVECTOR`

**Indexes (GIN — P2):**

| Table | Index |
|-------|-------|
| `categories` | GIN `search_vector` |
| `categories` | GIN trgm `name` |
| `products` | GIN `search_vector` |
| `products` | GIN trgm `name` |
| `product_variants` | GIN trgm `sku` |

**Optional P3:** `inventory_stock` low-stock partial index

**Backfill:** run `UPDATE commerce.products SET updated_at = updated_at` to fire search trigger; same for categories

**RLS:** none  

**Seed:** none  

---

### `000025_rls_policies_v3`

| Attribute | Detail |
|-----------|--------|
| **Depends on** | ALL table migrations `000012`–`000020`, `000017` payment_customers move |
| **Blocks** | `000026` |

**Drop obsolete policies:**
- `public.payment_customers` policies (table gone after 017)
- Review if any v1 policies conflict — do not drop `identity.users` / `public.user_*` policies

**Enable RLS + create policies:**

| Schema | Table | Policies |
|--------|-------|----------|
| `commerce` | `carts` | SELECT/INSERT/UPDATE own `user_id` |
| `commerce` | `cart_items` | SELECT/INSERT/UPDATE/DELETE via cart ownership join |
| `commerce` | `wishlist_items` | SELECT/INSERT/DELETE own `user_id` |
| `commerce` | `orders` | SELECT own `user_id` |
| `commerce` | `order_items` | SELECT via order ownership join |
| `commerce` | `returns` | SELECT own `user_id` |
| `commerce` | `reviews` | SELECT approved OR own pending; INSERT/UPDATE own |
| `commerce` | `payment_customers` | SELECT own `user_id` |

**Explicitly RLS OFF (document grants):** all other `commerce.*`, all `ops.*`, all `inventory` tables

**Seed:** none  

---

### `000026_seed_reference_data`

| Attribute | Detail |
|-----------|--------|
| **Depends on** | `000012`, `000014`, `000007` (roles exist) |
| **Blocks** | none — Phase 1 complete |

**Seed data requirements:**

| Entity | Data | Idempotent key |
|--------|------|----------------|
| `commerce.warehouses` | 1 default warehouse `WH-DEFAULT-01`, Mumbai, `is_default=true`, `priority=0` | `code` |
| `commerce.categories` | Root categories: `Women`, `Men`, `Kids`, `Accessories` (slugs) | `slug` |
| `admin.permissions` | Add if missing: `commerce.*`, `inventory.*`, `orders.refund` | `code` |
| `admin.role_permissions` | Map new permissions to `super_admin`, `inventory_manager`, etc. | role+permission |
| `commerce.coupons` | **Optional** dev-only `WELCOME10` — omit in production seed flag | `code_normalized` |

**No seed:** products, orders, users, payments

**Environment flag:** document `SEED_DEV_COUPON=true` for local/docker only

---

## 6. Seed data specification

### 6.1 Required (all environments)

```
warehouse:
  code: WH-DEFAULT-01
  name: Chic A Boo Default Warehouse
  is_active: true
  is_default: true
  priority: 0
  address: { city: Mumbai, state: Maharashtra, country: IN }

categories (parent_id NULL):
  - women    / Women
  - men      / Men
  - kids     / Kids
  - accessories / Accessories
```

### 6.2 RBAC additions (`000026`)

| Permission code | Roles |
|-----------------|-------|
| `inventory.read` | super_admin, admin, inventory_manager |
| `inventory.write` | super_admin, inventory_manager |
| `orders.refund` | super_admin, admin, customer_support |
| `coupons.write` | super_admin, admin, marketing_manager |
| `coupons.read` | super_admin, admin, marketing_manager, customer_support |

(Extend — do not duplicate existing v2 permissions from `000007`/`000009`)

### 6.3 Optional dev-only

| Entity | Purpose |
|--------|---------|
| Sample coupon `WELCOME10` | 10% off, min order ₹500 |
| 2–3 test products | manual QA — **not** in migration; use Admin API or fixture script |

---

## 7. RLS migration order (summary)

| Step | Migration | Action |
|------|-----------|--------|
| 1 | `000006` (done) | RLS v1 identity + public |
| 2 | `000009` (done) | RLS devices, login_history, public.payment_customers |
| 3 | `000017` | **DROP** policies on `public.payment_customers` before table drop |
| 4 | `000025` | Enable RLS + policies on commerce customer tables |
| 5 | `000025` | Enable RLS + SELECT on `commerce.payment_customers` |
| 6 | — | Never enable RLS on ops, inventory, payments, webhooks |

**Rule:** No RLS policies reference tables that do not yet exist — hence `000025` is late in the chain.

---

## 8. Index creation order (summary)

| Phase | Migration | Index types |
|-------|-----------|-------------|
| A | `000012`–`000019` | PK, FK B-tree, UNIQUE partial, status partial |
| B | `000023` | `audit_logs (domain, created_at)` |
| C | `000024` | GIN tsvector, GIN pg_trgm, category path trigger |
| D | `000024` | Optional P3 low-stock partial |

**Do not** create GIN indexes before seed/backfill in dev — production starts empty so order matters less, but `000024` before `000026` ensures seed categories get search vectors when products are added later.

---

## 9. Backfill strategies

### 9.1 `public.payment_customers` → `commerce.payment_customers` (`000017`)

```
1. CREATE commerce.payment_customers (same structure + schema)
2. INSERT INTO commerce.payment_customers SELECT * FROM public.payment_customers
3. DROP POLICY on public.payment_customers
4. DROP TRIGGER on public.payment_customers
5. DROP TABLE public.payment_customers
6. Verify row counts match
```

**Rollback plan:** keep `public.payment_customers` until INSERT verified; use transaction.

**FK references:** none from other live tables yet.

---

### 9.2 `identity.email_otps` → `identity.email_verifications` (`000021`)

```
1. ALTER TABLE identity.email_otps RENAME TO email_verifications
2. ADD COLUMN email_normalized TEXT
3. UPDATE email_verifications SET email_normalized = lower(trim(email))
4. ALTER email_normalized SET NOT NULL
5. ADD COLUMN purpose TEXT DEFAULT 'registration'
6. ADD COLUMN max_attempts INTEGER DEFAULT 3
7. ADD COLUMN verified_at TIMESTAMPTZ NULL
8. UPDATE verified_at = created_at WHERE verified = true
9. Rename indexes email_otps_* → email_verifications_*
```

**Application:** UserService must read new table name before deploy — coordinate deploy order: migration first, then service.

---

### 9.3 `admin.audit_logs` new columns (`000023`)

```sql
UPDATE admin.audit_logs
SET actor_type = 'admin', service_name = 'admin'
WHERE actor_type IS NULL;
```

No NOT NULL constraint on new columns — historical rows stay valid.

---

### 9.4 `public.user_preferences` (`000022`)

ADD COLUMN with DEFAULT — PostgreSQL auto-fills existing rows. No manual UPDATE.

---

### 9.5 Search vectors (`000024`)

After triggers installed:

```
UPDATE commerce.categories SET updated_at = now();
-- products: no rows yet at first deploy; document for reindex job
```

**Future job:** `commerce.reindex_search_vectors()` admin endpoint or migration utility.

---

### 9.6 `user_profiles.avatar_url` → `avatar_r2_key` (optional — Phase 1.5)

**Not in 000011–000026.** If pursued later:

1. ADD `avatar_r2_key` TEXT
2. Backfill: parse R2 key from URL where possible
3. Deprecate `avatar_url` column in application
4. DROP `avatar_url` in separate migration after app cutover

---

## 10. Deployment checklist

### Pre-migration

- [ ] Backup Supabase (point-in-time if Pro; pg_dump if not)
- [ ] Confirm `schema_migrations` shows `000001`–`000010`
- [ ] Set `database/.env` to session pooler `aws-1-ap-south-1:5432`
- [ ] Notify team: brief write freeze optional (additive migrations)

### Execute

```bash
cd chicaboo-backend/database
source .venv/bin/activate
python migrate.py status    # expect 000001-000010 applied
python migrate.py migrate   # applies 000011-000026 sequentially
python migrate.py status    # all green
```

### Post-migration verification

| Check | Query / action |
|-------|----------------|
| Schemas exist | `commerce`, `ops` |
| Table count | ~51 total |
| payment_customers location | `commerce.payment_customers` only |
| email table name | `identity.email_verifications` |
| Sequences | order_number_seq current value |
| RLS enabled | commerce carts, orders |
| Seed warehouse | `SELECT * FROM commerce.warehouses WHERE is_default` |
| Search trigger | insert test product; `search_vector` not null |

### Service deploy order

1. Run migrations `000011`–`000023` (safe before app changes)
2. Run `000024`–`000026`
3. Deploy UserService (email_verifications table name)
4. Deploy Backend (commerce schema models)
5. Deploy Admin (audit log columns)

---

## 11. Phase 2 roadmap (out of scope — numbers reserved)

| Migration | Name | Trigger |
|-----------|------|---------|
| `000027` | `catalog_vendors` | Marketplace launch |
| `000028` | `attribute_eav` | JSONB option_values insufficient |
| `000029` | `in_app_notifications` | Mobile app launch |
| `000030` | `log_partitions` | Supabase Pro + >2M audit rows |
| `000031` | `loyalty_transactions` | Loyalty program launch |
| `000032` | `identity_blocked_tokens` | Compliance requirement |

---

## 12. Migration file naming convention

```
{number}_{snake_case_description}.sql

Examples:
  000011_create_commerce_and_ops_schemas.sql
  000017_commerce_payments.sql
```

Numbers are **global** across all phases. Never renumber applied migrations.

---

## 13. Risk register

| Risk | Mitigation |
|------|------------|
| `payment_customers` move breaks running app | Deploy migration before service; table empty in prod today |
| FK addition order wrong | Deferred FKs documented in 014 → 016 |
| GIN index build locks categories | Run 024 off-peak; tables empty at first deploy |
| Supabase size limit | No bulk seed in migrations |
| PgBouncer + long DDL | One migration per file; avoid combined heavy DDL |
| email_otps rename | Coordinate UserService release |

---

## 14. Sign-off

| Item | Status |
|------|--------|
| Architecture v3.0 | Approved |
| Migration blueprint | **Ready for SQL authoring** |
| SQL files `000011`–`000026` | Not started |

---

*Chic A Boo Migration Blueprint v1.0 — aligns with `ARCHITECTURE_v3.md`*
