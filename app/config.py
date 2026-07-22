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

    # Message Central (VerifyNow) SMS OTP
    message_central_customer_id: str = ""
    message_central_email: str = ""
    message_central_password: str = ""
    message_central_country_code: str = "91"
    message_central_otp_length: int = 6

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

    sentry_dsn: str = ""

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
