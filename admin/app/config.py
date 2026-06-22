from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    port: int = 4003
    database_url: str = "postgres://user:pass@localhost:5432/chicaboo"
    redis_url: str = "redis://localhost:6379"
    admin_jwt_secret: str = ""


settings = Settings()
