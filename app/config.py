from __future__ import annotations

import sys
from pathlib import Path

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Monorepo root on sys.path for `shared.*`
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.config_utils import load_pem


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    port: int = 8000
    app_env: str = "development"
    log_level: str = "info"

    database_url: str = "postgresql+asyncpg://chicaboo:chicaboo@localhost:5433/chicaboo"
    redis_url: str = "redis://localhost:6379"

    cors_origins: str = "http://localhost:3000,http://localhost:3001"

    # Customer JWT (RS256)
    jwt_private_key: str = ""
    jwt_private_key_path: str = ""
    jwt_public_key: str = ""
    jwt_public_key_path: str = ""
    jwt_refresh_secret: str = ""
    jwt_access_ttl_seconds: int = 900
    jwt_refresh_ttl_seconds: int = 604800

    otp_ttl_seconds: int = 600
    password_reset_ttl_seconds: int = 3600

    # Public site URL (password reset links, email branding)
    site_url: str = "https://www.chicaboo.co"

    # Email — Resend primary, SMTP fallback
    resend_api_key: str = ""
    email_from: str = "noreply@chicaboo.co"
    email_from_name: str = "Chic A Boo"
    email_reply_to: str = "support@chicaboo.co"
    email_admin: str = "admin@chicaboo.co"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_secure: bool = False
    smtp_user: str = ""
    smtp_pass: str = ""

    # MSG91 — the SMS transport. Sends via the Flow API, which renders a
    # DLT-registered template from named variables on MSG91's side.
    #
    # MSG91's OTP product is deliberately not used: it would generate and
    # validate the code itself, which is precisely what made the previous
    # provider a dead end. We issue and verify our own codes.
    msg91_enabled: bool = True
    msg91_auth_key: str = ""
    #: Default DLT-registered template. Per-notification ids override this via
    #: ops.notification_templates.provider_template_id.
    msg91_template_id: str = ""
    #: 6-char alphanumeric header approved by the DLT registry.
    msg91_sender_id: str = ""
    msg91_base_url: str = "https://control.msg91.com"
    msg91_flow_path: str = "/api/v5/flow"

    #: Provider-agnostic phone settings. These were previously namespaced under
    #: the SMS vendor, which meant swapping vendors touched unrelated call sites.
    sms_country_code: str = "91"
    otp_length: int = 6

    # --- WhatsApp Business Platform (Meta Cloud API) -------------------------
    # Server-side only. WHATSAPP_ACCESS_TOKEN and WHATSAPP_APP_SECRET must never
    # reach a client; nothing here is exposed through a NEXT_PUBLIC_* variable.
    whatsapp_enabled: bool = False
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_business_account_id: str = ""
    whatsapp_app_id: str = ""
    whatsapp_app_secret: str = ""
    #: Echoed back on Meta's GET hub challenge when registering the webhook.
    whatsapp_verify_token: str = ""
    #: Meta signs webhook bodies with the app secret; this overrides it if set.
    whatsapp_webhook_secret: str = ""
    whatsapp_api_version: str = "v21.0"
    whatsapp_default_language: str = "en"

    # --- Channel policy ------------------------------------------------------
    notification_primary_provider: str = "msg91"

    # SMS is the only live phone channel. These defaults used to be "whatsapp",
    # which meant any deployment that did not explicitly set the env vars sent
    # every OTP to WhatsApp first — where no template is approved, so it failed
    # and burned ~2s before falling back. Defaulting to the channel that actually
    # works keeps a missing env var from routing to a dead one. Flip back to
    # "whatsapp" once the business is verified and Meta approves the templates.
    otp_primary_channel: str = "sms"
    otp_fallback_channel: str = "sms"
    #: No fallback: SMS is both primary and the only option, and attempting a
    #: second channel that cannot deliver only delays the error the caller needs.
    otp_whatsapp_fallback_enabled: bool = False

    transactional_primary_channel: str = "sms"
    transactional_fallback_channel: str = "sms"

    marketing_primary_channel: str = "whatsapp"
    #: Deliberately False. A failed marketing WhatsApp must not silently become a
    #: paid SMS; campaigns opt in individually.
    marketing_sms_fallback: bool = False

    #: What to do when WhatsApp neither confirms nor denies (timeout).
    #: never      — wait for reconciliation only (safest, may delay the OTP)
    #: reconcile  — re-check with Meta, fall back only if still unresolved
    #: immediate  — fall back at once (most duplicates)
    otp_unknown_fallback_policy: str = "reconcile"
    #: How long to wait for a delivery signal before the reconcile policy acts.
    otp_unknown_reconcile_seconds: int = 20

    # OTP lifecycle
    otp_max_verify_attempts: int = 5
    otp_resend_cooldown_seconds: int = 60
    #: Max OTP requests per destination per hour.
    rate_limit_otp_per_phone_hourly: int = 5
    #: Max OTP requests per IP per hour.
    rate_limit_otp_per_ip_hourly: int = 20

    max_failed_login_attempts: int = 5
    account_lockout_minutes: int = 30

    rate_limit_login: int = 10
    rate_limit_register: int = 5
    rate_limit_verify_email: int = 10
    rate_limit_forgot_password: int = 5
    rate_limit_reset_password: int = 10
    rate_limit_refresh: int = 30
    rate_limit_phone_otp: int = 5
    rate_limit_resend_verification: int = 5

    # Admin JWT (HS256)
    admin_jwt_secret: str = ""
    admin_jwt_ttl_seconds: int = 28800
    admin_mfa_issuer: str = "Chic A Boo Admin"

    # R2 — storefront naming
    r2_bucket: str = "chicaboo-assets"
    r2_account_id: str = ""
    r2_access_key: str = ""
    r2_secret_key: str = ""
    r2_endpoint_url: str = ""
    r2_public_base_url: str = ""

    # R2 — identity/admin naming (aliases; prefer filled from above if empty)
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket_name: str = ""

    avatar_max_size_bytes: int = 5 * 1024 * 1024
    avatar_upload_url_ttl_seconds: int = 900
    avatar_get_url_ttl_seconds: int = 3600

    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""

    # Storefront checkout / commerce
    shipping_flat_paise: int = 0
    free_shipping_threshold_paise: int = 0  # 0 disables free-shipping logic
    # Default GST rate in basis points applied when a product has no explicit
    # tax_rate_bps in metadata (1800 = 18%, 500 = 5%, 0 = exempt).
    default_gst_rate_bps: int = 0
    # Prices in the catalog are GST-inclusive (Indian retail convention).
    prices_include_gst: bool = True

    # Company / seller details printed on the GST tax invoice
    company_legal_name: str = "Chic A Boo"
    company_address_line1: str = ""
    company_address_line2: str = ""
    company_city: str = ""
    company_state: str = ""
    company_state_code: str = ""  # GST state code, e.g. "09" for Uttar Pradesh
    company_postal_code: str = ""
    company_country: str = "India"
    company_gstin: str = ""
    company_pan: str = ""
    company_email: str = "support@chicaboo.co"
    company_phone: str = ""
    invoice_prefix: str = "CAB"
    invoice_r2_prefix: str = "invoices"

    sentry_dsn: str = ""

    @property
    def razorpay_configured(self) -> bool:
        return bool(self.razorpay_key_id and self.razorpay_key_secret)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def whatsapp_configured(self) -> bool:
        return bool(
            self.whatsapp_enabled
            and self.whatsapp_access_token
            and self.whatsapp_phone_number_id
        )

    @property
    def whatsapp_signing_secret(self) -> str:
        """Meta signs webhooks with the app secret unless one is set explicitly."""
        return self.whatsapp_webhook_secret or self.whatsapp_app_secret

    @property
    def database_dsn(self) -> str:
        url = self.database_url.strip()
        if url.endswith("?"):
            return url[:-1]
        return url

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() in {"production", "prod"}

    @computed_field  # type: ignore[prop-decorator]
    @property
    def jwt_private_key_pem(self) -> str:
        return load_pem(self.jwt_private_key, self.jwt_private_key_path)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def jwt_public_key_pem(self) -> str:
        return load_pem(self.jwt_public_key, self.jwt_public_key_path)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def r2_endpoint(self) -> str:
        if self.r2_endpoint_url:
            return self.r2_endpoint_url.rstrip("/")
        if self.r2_account_id:
            return f"https://{self.r2_account_id}.r2.cloudflarestorage.com"
        return ""

    @property
    def effective_r2_access_key_id(self) -> str:
        return self.r2_access_key_id or self.r2_access_key

    @property
    def effective_r2_secret_access_key(self) -> str:
        return self.r2_secret_access_key or self.r2_secret_key

    @property
    def effective_r2_bucket_name(self) -> str:
        return self.r2_bucket_name or self.r2_bucket

    @property
    def r2_configured(self) -> bool:
        return bool(
            self.effective_r2_access_key_id
            and self.effective_r2_secret_access_key
            and self.effective_r2_bucket_name
            and self.r2_endpoint
        )


settings = Settings()
