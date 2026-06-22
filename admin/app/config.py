import sys
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
_docker_shared = Path(__file__).resolve().parents[1] / "shared"
if _docker_shared.is_dir():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    port: int = 4003
    app_env: str = "development"
    log_level: str = "info"

    database_url: str = "postgresql+asyncpg://chicaboo:chicaboo@localhost:5433/chicaboo"
    redis_url: str = "redis://localhost:6379"

    admin_jwt_secret: str = ""
    admin_jwt_ttl_seconds: int = 28800
    admin_mfa_issuer: str = "ChicABoo Admin"
    sentry_dsn: str = ""


settings = Settings()
