"""Core configuration."""
import secrets
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_NAME: str = "Sindh IT Ticket System"
    APP_VERSION: str = "2.0.0"
    DATABASE_URL: str = "sqlite+aiosqlite:///./sindh_tickets.db"
    SECRET_KEY: str = secrets.token_hex(32)
    SESSION_MAX_AGE: int = 1209600  # 14 days
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_MODEL: str = "meta-llama/llama-3.1-8b-instruct:free"
    OPENROUTER_URL: str = "https://openrouter.ai/api/v1/chat/completions"
    UPLOAD_DIR: str = "./uploads"
    MAX_UPLOAD_SIZE: int = 10 * 1024 * 1024  # 10MB
    RATE_LIMIT_LOGIN: str = "5/minute"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
