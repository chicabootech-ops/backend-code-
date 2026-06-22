from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    port: int = 4001
    database_url: str = "postgres://user:pass@localhost:5432/chicaboo"
    redis_url: str = "redis://localhost:6379"
    jwt_private_key: str = ""
    jwt_public_key: str = ""
    jwt_refresh_secret: str = ""
    smtp_host: str = ""
    smtp_user: str = ""
    smtp_pass: str = ""


settings = Settings()
