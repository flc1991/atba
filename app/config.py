from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    SECRET_KEY: str = "dev-secret-key-change-in-production"
    DEBUG: bool = False
    BASE_URL: str = "http://localhost:8000"

    DATABASE_URL: str = "sqlite:///./instance/atba.db"

    PAYPAL_CLIENT_ID: str = ""
    PAYPAL_CLIENT_SECRET: str = ""
    PAYPAL_MODE: str = "sandbox"  # "sandbox" or "live"
    # PayPal Standard Payments (form-redirect flow): merchant email collected by PayPal.
    PAYPAL_BUSINESS_EMAIL: str = ""

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
    def paypal_checkout_url(self) -> str:
        """Endpoint for PayPal Standard Payments form POST (cgi-bin/webscr)."""
        if self.PAYPAL_MODE == "live":
            return "https://www.paypal.com/cgi-bin/webscr"
        return "https://www.sandbox.paypal.com/cgi-bin/webscr"


settings = Settings()
