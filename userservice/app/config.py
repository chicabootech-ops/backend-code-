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
    max_failed_login_attempts: int = 5
    account_lockout_minutes: int = 30
    sentry_dsn: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def jwt_private_key_pem(self) -> str:
        return load_pem(self.jwt_private_key, self.jwt_private_key_path)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def jwt_public_key_pem(self) -> str:
        return load_pem(self.jwt_public_key, self.jwt_public_key_path)


settings = Settings()
