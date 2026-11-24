from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    SECRET_KEY: str = "dev-secret-key-change-in-production"
    DEBUG: bool = False
    BASE_URL: str = "http://localhost:8000"

    DATABASE_URL: str = "sqlite:///./instance/atba.db"

    PAYPAL_CLIENT_ID: str = ""
    PAYPAL_CLIENT_SECRET: str = ""
    PAYPAL_MODE: str = "sandbox"  # "sandbox" or "live"
    PAYPAL_WEBHOOK_ID: str = ""

    # Check payments are offered only when both are set.
    CHECK_PAYEE_NAME: str = ""
    CHECK_MAILING_ADDRESS: str = ""

    MAIL_USERNAME: str = ""
    MAIL_PASSWORD: str = ""
    MAIL_FROM: str = ""
    MAIL_SERVER: str = "smtp.gmail.com"
    MAIL_PORT: int = 587
    MAIL_STARTTLS: bool = True
    MAIL_SSL_TLS: bool = False

    @property
    def paypal_base_url(self) -> str:
        if self.PAYPAL_MODE == "live":
            return "https://api-m.paypal.com"
        return "https://api-m.sandbox.paypal.com"

    @property
    def paypal_enabled(self) -> bool:
        """True once real credentials (not the .env.example placeholders) are set."""
        keys = (self.PAYPAL_CLIENT_ID, self.PAYPAL_CLIENT_SECRET)
        return all(k and not k.startswith("your-") for k in keys)


settings = Settings()
