import sys
from pathlib import Path

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
_docker_shared = Path(__file__).resolve().parents[1] / "shared"
if _docker_shared.is_dir():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.config_utils import load_pem


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    port: int = 4001
    app_env: str = "development"
    log_level: str = "info"

    database_url: str = "postgresql+asyncpg://chicaboo:chicaboo@localhost:5433/chicaboo"
    redis_url: str = "redis://localhost:6379"

    jwt_private_key: str = ""
    jwt_private_key_path: str = ""
    jwt_public_key: str = ""
    jwt_public_key_path: str = ""
    jwt_refresh_secret: str = ""

    resend_api_key: str = ""
    email_from: str = "noreply@chicaboo.com"
    smtp_host: str = ""
    smtp_user: str = ""
    smtp_pass: str = ""

    jwt_access_ttl_seconds: int = 900
    jwt_refresh_ttl_seconds: int = 604800
    otp_ttl_seconds: int = 600
    password_reset_ttl_seconds: int = 3600

    max_failed_login_attempts: int = 5
    account_lockout_minutes: int = 30

    rate_limit_login: int = 10
    rate_limit_register: int = 5
    rate_limit_verify_email: int = 10
    rate_limit_forgot_password: int = 5
    rate_limit_reset_password: int = 10
    rate_limit_refresh: int = 30

    sentry_dsn: str = ""

    # Cloudflare R2
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket_name: str = "chicaboo-assets"
    r2_endpoint_url: str = ""
    avatar_max_size_bytes: int = 5 * 1024 * 1024
    avatar_upload_url_ttl_seconds: int = 900
    avatar_get_url_ttl_seconds: int = 3600

    @computed_field  # type: ignore[prop-decorator]
    @property
    def r2_endpoint(self) -> str:
        if self.r2_endpoint_url:
            return self.r2_endpoint_url.rstrip("/")
        if self.r2_account_id:
            return f"https://{self.r2_account_id}.r2.cloudflarestorage.com"
        return ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def jwt_private_key_pem(self) -> str:
        return load_pem(self.jwt_private_key, self.jwt_private_key_path)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def jwt_public_key_pem(self) -> str:
        return load_pem(self.jwt_public_key, self.jwt_public_key_path)


settings = Settings()
