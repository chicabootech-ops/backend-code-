import sys
from pathlib import Path

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
# Docker: shared lives at /app/shared alongside /app/app
_docker_shared = Path(__file__).resolve().parents[1] / "shared"
if _docker_shared.is_dir():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.config_utils import load_pem


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    port: int = 8000
    app_env: str = "development"
    log_level: str = "info"

    jwt_public_key: str = ""
    jwt_public_key_path: str = ""

    user_service_url: str = "http://localhost:4001"
    backend_url: str = "http://localhost:4002"
    admin_url: str = "http://localhost:4003"
    redis_url: str = "redis://localhost:6379"
    cors_origins: str = "http://localhost:3000"
    sentry_dsn: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def jwt_public_key_pem(self) -> str:
        return load_pem(self.jwt_public_key, self.jwt_public_key_path)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
