from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    port: int = 8000
    jwt_public_key: str = ""
    user_service_url: str = "http://localhost:4001"
    backend_url: str = "http://localhost:4002"
    admin_url: str = "http://localhost:4003"
    redis_url: str = "redis://localhost:6379"
    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
