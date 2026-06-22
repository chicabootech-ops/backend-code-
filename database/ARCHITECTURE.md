# Chic A Boo — Production Database Architecture (v1.1)

**Status:** Review complete · Migrations `000008`–`000009` ready  
**Database:** PostgreSQL 15 (Supabase) · Schemas: `auth`, `public`, `admin`  
**Stack:** UUID PKs · TIMESTAMPTZ · TEXT · JSONB · soft delete · CHECK constraints · RLS

---

## 1. Architecture Review

### 1.1 Current foundation (v1.0)

The v1.0 schema correctly separates concerns across three schemas:

| Schema | Responsibility | RLS |
|--------|----------------|-----|
| `auth` | Credentials, tokens (hashed), security events | On `users` only |
| `public` | Customer profile, addresses, preferences | Enabled |
| `admin` | RBAC, admin users, audit trail | Disabled (service role) |

**Strengths**

- UUID primary keys throughout — safe for distributed services and public exposure.
- Soft deletes on all durable customer/admin entities.
- Redis owns ephemeral auth state (OTP, refresh rotation, rate limits) while DB stores hashes and audit records.
- Partial unique indexes on `deleted_at IS NULL` support re-registration without hard deletes.
- CHECK constraints instead of PG ENUMs — migrations remain additive.

**Gaps addressed in v1.1**

| Gap | Resolution |
|-----|------------|
| No human-readable customer ID | `customer_number` sequence from 100001 |
| No account lockout persistence | `failed_login_attempts`, `locked_until` |
| Case-sensitive email uniqueness | `email_normalized` + unique partial index |
| Weak address modelling | `address_type`, `custom_label` |
| Opaque suspension reasons | `status_reason` |
| Thin audit trail | `request_id`, `user_agent`, `target_user_id` |
| No admin MFA storage | `mfa_enabled`, `mfa_secret`, `last_mfa_at` |
| No device/session visibility | `auth.user_devices`, `auth.login_history`, `admin.admin_sessions` |
| No payment provider mapping | `public.payment_customers` |
| Over-restrictive phone uniqueness | Option B + verified-phone partial unique |
| Permission vocabulary inconsistent | v2 naming: `{resource}.{action}` |

### 1.2 Data ownership model

```
┌─────────────────────────────────────────────────────────────┐
│                        PostgreSQL                           │
│  Permanent records · audit · RBAC · provider mappings     │
└─────────────────────────────────────────────────────────────┘
         ▲                              ▲
         │                              │
   UserService                    Backend / Admin
         │                              │
┌────────┴────────┐            ┌────────┴────────┐
│  Upstash Redis  │            │  Cloudflare R2  │
│ OTP · refresh   │            │ Images · PDFs   │
│ rate limits     │            └─────────────────┘
│ session cache   │
└─────────────────┘
```

**Rule:** Plaintext tokens, OTPs, and active sessions never touch PostgreSQL.

---

## 2. Recommended Schema Changes (v1.1)

### 2.1 `auth.users` — updated definition

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | Unchanged |
| `customer_number` | BIGINT UNIQUE | Auto-increment from 100001; display as "Customer #100001" |
| `email` | TEXT NOT NULL | Original casing preserved for display |
| `email_normalized` | TEXT NOT NULL | `lower(trim(email))` — uniqueness enforced here |
| `phone` | TEXT NULL | Non-unique; verified phones partially unique |
| `password_hash` | TEXT NOT NULL | Argon2id |
| `email_verified` | BOOLEAN | Default FALSE |
| `phone_verified` | BOOLEAN | Default FALSE |
| `status` | TEXT | `active`, `suspended`, `blocked`, `pending_verification` |
| `status_reason` | TEXT NULL | Human-readable suspension/block context |
| `failed_login_attempts` | INTEGER | Default 0; reset on successful login |
| `locked_until` | TIMESTAMPTZ NULL | Temporary lockout expiry |
| `last_login_at` | TIMESTAMPTZ NULL | |
| `created_at` / `updated_at` / `deleted_at` | TIMESTAMPTZ | Standard |

**Indexes**

```sql
UNIQUE (customer_number)
UNIQUE (email_normalized) WHERE deleted_at IS NULL
INDEX (email)                          -- display lookup
INDEX (phone) WHERE deleted_at IS NULL AND phone IS NOT NULL
UNIQUE (phone) WHERE deleted_at IS NULL AND phone IS NOT NULL AND phone_verified = TRUE
INDEX (status)
INDEX (locked_until) WHERE locked_until IS NOT NULL
INDEX (created_at)
```

### 2.2 `public.user_addresses` — updated definition

| Column | Type | Notes |
|--------|------|-------|
| `address_type` | TEXT NOT NULL | `shipping`, `billing`, `home`, `office`, `other` |
| `custom_label` | TEXT NULL | User-defined label when `address_type = 'other'` |
| `label` | TEXT | Legacy/display label (retained) |

**Indexes**

```sql
INDEX (user_id)
INDEX (postal_code)
INDEX (address_type)
INDEX (user_id, address_type) WHERE deleted_at IS NULL
```

### 2.3 `admin.audit_logs` — updated definition

| Column | Type | Purpose |
|--------|------|---------|
| `request_id` | TEXT | Correlate with Gateway `X-Request-ID` and distributed traces |
| `user_agent` | TEXT | Forensics for admin actions (detect scripted abuse) |
| `target_user_id` | UUID FK → `auth.users` | When admin action affects a customer (ban, refund, profile edit) |

**Indexes**

```sql
INDEX (admin_id)
INDEX (entity_type, entity_id)    -- composite lookup
INDEX (created_at)
INDEX (request_id) WHERE request_id IS NOT NULL
INDEX (target_user_id) WHERE target_user_id IS NOT NULL
```

### 2.4 `admin.admin_users` — MFA columns

| Column | Type | Purpose |
|--------|------|---------|
| `mfa_enabled` | BOOLEAN DEFAULT FALSE | Gate admin login step 2 |
| `mfa_secret` | TEXT NULL | Encrypted TOTP secret (see §8) |
| `last_mfa_at` | TIMESTAMPTZ NULL | Last successful MFA verification |

---

## 3. New Tables

### 3.1 `auth.user_devices`

Tracks known devices per customer for security UX ("Sign out other devices").

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `user_id` | UUID FK | → `auth.users` |
| `device_name` | TEXT | e.g. "iPhone 15", "Chrome on Windows" |
| `device_type` | TEXT | `mobile`, `tablet`, `desktop`, `unknown` |
| `ip_address` | TEXT | Last known IP |
| `user_agent` | TEXT | Raw UA string |
| `last_seen_at` | TIMESTAMPTZ | Updated on each authenticated request |
| `created_at` | TIMESTAMPTZ | First seen |
| `revoked_at` | TIMESTAMPTZ NULL | Soft revoke (logout device) |

**Indexes:** `(user_id)`, `(last_seen_at)`, `(user_id, last_seen_at DESC) WHERE revoked_at IS NULL`

**Security:** RLS enabled — users see only their own active devices. Inserts/updates via UserService with `app.current_user_id`. Revocation sets `revoked_at`, never hard delete.

### 3.2 `public.payment_customers`

Maps internal users to external payment provider customer IDs.

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `user_id` | UUID FK | → `auth.users` |
| `provider` | TEXT | `razorpay`, `stripe`, `payu`, `cashfree` (CHECK, extensible) |
| `provider_customer_id` | TEXT | Razorpay `cust_xxx` |
| `metadata` | JSONB | Provider-specific fields |
| `created_at` / `updated_at` / `deleted_at` | TIMESTAMPTZ | Soft delete |

**Uniqueness**

- One active mapping per `(user_id, provider)`
- One active mapping per `(provider, provider_customer_id)`

**Security:** RLS SELECT for owner; writes service-only.

### 3.3 `auth.login_history` (foundational)

Structured login audit distinct from `security_logs` (which captures broader security events).

| Column | Type | Notes |
|--------|------|-------|
| `user_id` | UUID FK NULL | NULL on failed unknown-email attempts |
| `email_attempted` | TEXT | Normalized email used at login |
| `success` | BOOLEAN | |
| `failure_reason` | TEXT | `invalid_password`, `account_locked`, `account_suspended` |
| `ip_address` / `user_agent` | TEXT | |
| `device_id` | UUID FK NULL | → `auth.user_devices` |
| `request_id` | TEXT | Gateway trace ID |
| `created_at` | TIMESTAMPTZ | |

**Retention:** Partition or archive after 90 days in production.

### 3.4 `admin.admin_sessions` (foundational)

DB record of admin sessions; active session state lives in Redis (`admin:session:{id}`).

| Column | Type | Notes |
|--------|------|-------|
| `admin_id` | UUID FK | |
| `session_token_hash` | TEXT UNIQUE | Never store plaintext |
| `ip_address` / `user_agent` | TEXT | |
| `mfa_verified` | BOOLEAN | TRUE after TOTP step |
| `expires_at` | TIMESTAMPTZ | 8 hours default |
| `revoked_at` | TIMESTAMPTZ NULL | Logout / forced revoke |

---

## 4. Email Normalization — Indexing Strategy

**Write path (UserService):**

```text
email           = user input (preserve casing)
email_normalized = lower(trim(email))
```

**Uniqueness:** Partial unique index on `email_normalized WHERE deleted_at IS NULL`.

**Why not `CITEXT`?** CHECK-constraint style and explicit normalization keep behaviour predictable across services and avoid implicit collation surprises on Supabase.

**Query patterns**

| Use case | Index used |
|----------|------------|
| Registration duplicate check | `users_email_normalized_unique_active` |
| Login lookup | `users_email_normalized_unique_active` (equality on normalized) |
| Admin search by display email | `users_email_idx` (optional `pg_trgm` GIN later) |
| Support lookup by customer # | `users_customer_number_unique` |

---

## 5. User Status Tracking — `status_reason`

| `status` | Example `status_reason` |
|----------|-------------------------|
| `suspended` | `admin suspension`, `fraud review` |
| `blocked` | `chargeback abuse`, `verification failure` |
| `pending_verification` | `email_not_verified` |
| `active` | NULL |

**Usage**

- **Admin service** sets `status` + `status_reason` on ban/suspend; writes `admin.audit_logs` with `target_user_id`.
- **UserService** sets `status_reason` on automated lockout (`too_many_failed_logins`).
- **Customer-facing API** returns generic message; `status_reason` exposed only to admin.

---

## 6. Permission Strategy Review

### 6.1 Naming convention

```
{resource}.{action}
```

| Action | Meaning |
|--------|---------|
| `read` | List and view |
| `write` | Create and update |
| `delete` | Soft-delete / deactivate |
| `refund` | Domain-specific (orders) |

### 6.2 Full permission registry (v2)

```
users.read · users.write · users.delete
products.read · products.write · products.delete
orders.read · orders.write · orders.refund
inventory.read · inventory.write
coupons.read · coupons.write
analytics.read · analytics.write
settings.read · settings.write
```

### 6.3 Maintainability recommendations

1. **Additive migrations** — never rename permission codes in place; insert new, map roles, deprecate old.
2. **Permission groups in application** — Admin UI groups by resource prefix (`users.*`).
3. **Super-admin bypass** — check `role.name = 'super_admin'` OR explicit permission; avoid duplicating all permissions on every request.
4. **Cache permissions in Redis** — `admin:perms:{admin_id}` TTL 5 min; invalidate on role change.
5. **v1 → v2 coexistence** — `products.update` retained; new code uses `products.write`. Remove v1 codes after all services migrate.

---

## 7. Admin MFA — Flow & Storage

### 7.1 Enrollment flow

1. Admin enables MFA → generate TOTP secret.
2. Store **encrypted** secret in `mfa_secret` (application-layer AES-256-GCM with `ADMIN_MFA_ENCRYPTION_KEY` env var).
3. Return `otpauth://` QR to admin once.
4. Set `mfa_enabled = TRUE` after first successful verification.

### 7.2 Login flow

1. Password verified → issue partial session (Redis, `mfa_verified = false`).
2. Admin submits TOTP → verify against decrypted secret.
3. Set `mfa_verified = true`, update `last_mfa_at`, create `admin.admin_sessions` row.

### 7.3 Storage considerations

| Concern | Mitigation |
|---------|------------|
| Secret at rest | Encrypt before INSERT; never log or return after enrollment |
| Secret in backups | Encryption key stored outside DB (Render env secret) |
| Recovery | Separate break-glass super-admin procedure; backup codes in `metadata` JSONB (hashed) |
| Redis vs DB | Redis = active session; DB = audit trail and forced revocation |

**Never** store MFA secrets in Redis long-term.

---

## 8. Phone Number Strategy — Indian E-commerce

### Option A: Global uniqueness

- **Pros:** One account per phone; simpler OTP login.
- **Cons:** Family/shared phones common in India; business lines reused; blocks re-registration after soft delete unless phone is released; COD fraud rings reuse numbers across accounts.

### Option B: Non-unique with indexing (recommended)

- **Pros:** Multiple accounts can share a phone (family, business); OTP verified at checkout independent of account uniqueness; soft-delete re-registration friction-free.
- **Cons:** Requires OTP verification before trusting phone for sensitive actions.

### Recommended: Option B + verified-phone partial unique

```sql
-- Lookup index (non-unique)
INDEX (phone) WHERE deleted_at IS NULL AND phone IS NOT NULL

-- Only one VERIFIED phone per active account
UNIQUE (phone) WHERE deleted_at IS NULL AND phone IS NOT NULL AND phone_verified = TRUE
```

This prevents two **verified** accounts claiming the same number while allowing unverified duplicates during onboarding.

---

## 9. Future Readiness Assessment

### 9.1 Ready to build now

With v1.1, these modules can proceed:

| Module | Root FKs available |
|--------|-------------------|
| Cart / Wishlist | `auth.users` |
| Orders / Order Items | `auth.users`, `public.user_addresses` |
| Payments | `auth.users`, `public.payment_customers` |
| Reviews | `auth.users` (add `verified_purchase` check later) |
| Notifications | `auth.users`, `public.user_preferences` |
| Shipping | `public.user_addresses` |
| Returns | `auth.users` + future `orders` |

### 9.2 Add before or with commerce modules

| Table | Schema | Priority | Reason |
|-------|--------|----------|--------|
| `products` | `public` | P0 | Core catalog |
| `categories` | `public` | P0 | Navigation |
| `product_variants` | `public` | P0 | SKU/stock unit |
| `inventory` | `public` | P0 | Stock counts |
| `product_images` | `public` | P0 | R2 URL references |
| `carts` / `cart_items` | `public` | P0 | Redis cache + DB persistence |
| `orders` / `order_items` | `public` | P0 | Transaction core |
| `payments` | `public` | P0 | Razorpay payment records |
| `order_status_history` | `public` | P1 | State machine audit |
| `coupons` | `public` | P1 | Promotions |
| `webhook_events` | `public` | P1 | Razorpay idempotency (`event_id` UNIQUE) |
| `idempotency_keys` | `public` | P1 | Safe retries on order create |
| `reviews` | `public` | P2 | Post-launch |
| `notification_log` | `public` | P2 | Resend delivery tracking |

### 9.3 Auth foundation verdict

**Sufficient for commerce build** after applying migrations `000008`–`000009`.

Remaining pre-commerce work is **catalog/order schema design**, not auth gaps.

---

## 10. Security Recommendations

1. **Service database role** — use `chicaboo_service` (BYPASSRLS) for UserService/Backend/Admin; never expose to clients.
2. **RLS session variable** — `SET LOCAL app.current_user_id = '<uuid>'` per transaction for customer-scoped reads.
3. **Lockout sync** — `failed_login_attempts`/`locked_until` in DB; rate limit in Redis (`rl:login:{ip}`) as first line.
4. **Audit immutability** — `admin.audit_logs` INSERT only; no UPDATE/DELETE grants.
5. **MFA secrets** — encrypt at application layer; rotate encryption key annually.
6. **PII in logs** — `login_history.email_attempted` is admin-only; never log passwords or tokens.
7. **Supabase** — disable Supabase Auth; use connection pooler (port 6543) for app; direct connection (5432) for migrations only.

---

## 11. RLS Recommendations

| Table | RLS | Policies |
|-------|-----|----------|
| `auth.users` | ON | SELECT/UPDATE own row |
| `auth.user_devices` | ON | SELECT/INSERT/UPDATE own |
| `auth.login_history` | ON | SELECT own (writes service-only) |
| `public.user_profiles` | ON | Full own-row CRUD |
| `public.user_addresses` | ON | Full own-row CRUD |
| `public.user_preferences` | ON | Full own-row CRUD |
| `public.payment_customers` | ON | SELECT own (writes service-only) |
| `auth.refresh_tokens` | OFF | Service only |
| `auth.email_otps` | OFF | Service only |
| `auth.password_resets` | OFF | Service only |
| `auth.security_logs` | OFF | Service only |
| All `admin.*` | OFF | Service only |

---

## 12. Migration Order

| Order | File | Description |
|-------|------|-------------|
| 1 | `000001_extensions_and_schemas.sql` | Extensions + schemas |
| 2 | `000002_auth_domain.sql` | Auth tables |
| 3 | `000003_public_domain.sql` | Customer tables |
| 4 | `000004_admin_domain.sql` | Admin tables |
| 5 | `000005_triggers.sql` | `updated_at` triggers |
| 6 | `000006_rls_policies.sql` | v1.0 RLS |
| 7 | `000007_seed_admin_rbac.sql` | Roles + v1 permissions |
| 8 | `000008_production_enhancements.sql` | **v1.1 schema changes + new tables** |
| 9 | `000009_permissions_v2_and_rls.sql` | Permission v2 + new RLS |

**Apply:**

```bash
cd database
export DATABASE_URL=postgresql://chicaboo:chicaboo@localhost:5433/chicaboo
python migrate.py migrate
```

**Supabase production:** use direct connection (port 5432), not pooler, for DDL migrations.

---

## 13. SQL Migration Plan (rollback notes)

| Change | Rollback complexity |
|--------|---------------------|
| `customer_number` | Hard — assigned numbers consumed |
| `email_normalized` | Medium — restore email unique index |
| Phone dedup drop | Low — re-add unique index if needed |
| New tables | Low — `DROP TABLE` if empty |
| Permission v2 | Low — delete new rows |

**Production deployment**

1. Maintenance window not required (additive DDL).
2. Run `000008` then `000009`.
3. Deploy UserService with email normalization write path.
4. Deploy Admin with new audit fields.
5. Backfill not needed — migration handles `email_normalized` and `customer_number`.

---

## 14. Production-Readiness Assessment

| Area | Score | Notes |
|------|-------|-------|
| Identity & auth | **Ready** | Lockout, devices, login history, normalized email |
| Customer profile | **Ready** | Addresses typed; payment mapping in place |
| Admin & RBAC | **Ready** | MFA storage, session audit, enhanced audit logs |
| Commerce | **Not started** | Catalog/order migrations next |
| Security | **Ready** | RLS, soft delete, hash-only tokens |
| Scalability | **Good** | Indexed FKs; Redis for hot paths; partition `login_history` at scale |
| Maintainability | **Good** | Permission v2 convention; additive migrations |

### Verdict

**The authentication and user-management foundation is production-ready after applying migrations `000008`–`000009`.** Proceed to catalog (`products`, `categories`, `inventory`) and transaction (`orders`, `payments`, `webhook_events`) schema design as the next phase.

---

## Appendix A — Environment wiring

| Service | Port | `DATABASE_URL` | `REDIS_URL` |
|---------|------|----------------|-------------|
| Gateway | 8000 | — | Yes |
| UserService | 4001 | Yes | Yes |
| Backend | 4002 | Yes | Yes |
| Admin | 4003 | Yes | Yes |
| migrate | — | Yes (psycopg2) | — |

Local: `localhost:5433` (Postgres), `localhost:6379` (Redis).  
Docker: `postgres:5432`, `redis:6379` via compose overrides.

JWT keys: `keys/jwt_private.pem`, `keys/jwt_public.pem` (gitignored).
