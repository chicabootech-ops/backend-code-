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

    #: Storefront origin used to build links inside WhatsApp messages (cart URL,
    #: product URL, retry-payment URL). Empty falls back to `site_url` — they are
    #: the same host in every current deployment, and defaulting rather than
    #: duplicating means a message can never link at an unset origin.
    frontend_url: str = ""

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

    #: Provider-agnostic phone settings. Deliberately not namespaced under a
    #: vendor: WhatsApp is the only transport today, and burying "what country
    #: are these numbers in" inside a vendor block is what made the last swap
    #: touch unrelated call sites.
    phone_country_code: str = "91"
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
    # WhatsApp is the ONLY delivery channel. Every SMS vendor has been removed:
    # OTP, order updates and marketing all ride Meta's Cloud API.
    #
    # The `Channel` enum and the `NotificationProvider` ABC are deliberately kept
    # even though there is one implementation, because the channel is stored on
    # every notification and attempt row. Adding a transport later is a new
    # provider class plus a registration line — not a schema change.
    otp_primary_channel: str = "whatsapp"
    transactional_primary_channel: str = "whatsapp"
    marketing_primary_channel: str = "whatsapp"

    # --- Retry policy --------------------------------------------------------
    # There is no second channel to fall back to, so a transient failure is
    # retried on WhatsApp itself. Attempt 1 is immediate; these are the delays
    # before attempts 2 and 3, after which the notification is FAILED.
    #
    # UNKNOWN (timeout) is NOT retried on this ladder — the message may already
    # have been delivered and Meta charges per conversation. It waits for the
    # webhook, and the reconciler resolves it if no signal ever arrives.
    notification_retry_delays_seconds: list[int] = [300, 900]
    notification_max_attempts: int = 3

    #: How long an UNKNOWN notification waits for a delivery webhook before the
    #: reconciler asks Meta directly what happened to it.
    notification_unknown_reconcile_seconds: int = 300

    # OTP lifecycle
    otp_max_verify_attempts: int = 5
    otp_resend_cooldown_seconds: int = 60
    #: Max OTP requests per destination per hour.
    rate_limit_otp_per_phone_hourly: int = 5
    #: Max OTP requests per IP per hour.
    rate_limit_otp_per_ip_hourly: int = 20

    # --- Campaigns & marketing ----------------------------------------------
    #: Recipients per campaign batch, and the pause between batches. Meta
    #: throttles per phone number id; pacing keeps a blast from burning quality
    #: rating, which the same number needs for OTP delivery.
    campaign_batch_size: int = 50
    campaign_batch_pause_seconds: float = 1.0

    #: Abandoned-cart ladder, in hours after the cart went quiet.
    cart_reminder_first_hours: int = 1
    cart_reminder_second_hours: int = 24
    cart_reminder_coupon_hours: int = 48
    #: Coupon handed out by the third reminder. Empty disables that rung.
    cart_reminder_coupon_code: str = ""

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
    def effective_frontend_url(self) -> str:
        """Storefront origin for links embedded in messages, without a trailing /.

        A trailing slash would produce `https://site.co//cart`, which most hosts
        serve but which looks broken in a WhatsApp link preview.
        """
        return (self.frontend_url or self.site_url).rstrip("/")

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
