from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    port: int = 4002
    database_url: str = "postgres://user:pass@localhost:5432/chicaboo"
    redis_url: str = "redis://localhost:6379"
    r2_bucket: str = "chicaboo-assets"
    r2_account_id: str = ""
    r2_access_key: str = ""
    r2_secret_key: str = ""
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""


settings = Settings()
