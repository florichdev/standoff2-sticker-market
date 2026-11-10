import json
from typing import Annotated, Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    BOT_TOKEN: str = Field(
        default="",
        description="Telegram Bot API Token from @BotFather",
    )
    OLD_BOT_TOKEN: str | None = Field(
        default=None,
        description="Legacy Bot API Token for automatic photo recovery",
    )
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/standoff_market",
        description="Async PostgreSQL connection string",
    )
    REDIS_URL: str | None = Field(
        default=None,
        description="Optional Redis connection URL for persistent FSM storage (e.g. redis://127.0.0.1:6379/0)",
    )
    TELEGRAM_PROXY: str | None = Field(
        default=None,
        description="Optional HTTP or SOCKS5 proxy URL for Telegram Bot API (e.g. http://127.0.0.1:10809)",
    )
    ADMIN_IDS: Annotated[list[int], NoDecode] = Field(
        default_factory=list,
        description="List of Telegram User IDs with admin access",
    )

    @field_validator("ADMIN_IDS", mode="before")
    @classmethod
    def parse_admin_ids(cls, v: Any) -> list[int]:
        """Parse unlimited admin IDs from list, comma-separated string, JSON, or integer."""
        if not v:
            return []
        if isinstance(v, list):
            return [int(x) for x in v if str(x).strip().isdigit()]
        if isinstance(v, int):
            return [v]
        if isinstance(v, str):
            val = v.strip()
            if not val:
                return []
            if val.startswith("[") and val.endswith("]"):
                try:
                    parsed = json.loads(val)
                    if isinstance(parsed, list):
                        return [int(x) for x in parsed if str(x).strip().isdigit()]
                except (json.JSONDecodeError, ValueError):
                    pass
            cleaned = val.replace(";", ",").replace(" ", ",")
            result: list[int] = []
            for item in cleaned.split(","):
                item_str = item.strip()
                if item_str.isdigit():
                    result.append(int(item_str))
            return result
        return []

    STARS_TURBO_PRICE: int = Field(
        default=150, description="Cost in Stars for 3-day Turbo promotion"
    )
    STARS_STANDARD_PRICE: int = Field(
        default=50, description="Cost in Stars for 24h Standard promotion"
    )
    DEFAULT_REFERRALS_FOR_TOP: int = Field(
        default=3,
        description="Default number of invited friends required to earn 1 free TOP promotion",
    )

    MAX_ACTIVE_ADS: int = Field(
        default=10, description="Maximum active listings allowed per seller"
    )
    AUTOPOST_CHANNEL_ID: str | None = Field(
        default=None,
        description="Telegram channel username (e.g. @standsticker_feed) or ID (-100...) for autoposting new listings",
    )
    AUTOPOST_ENABLED: bool = Field(
        default=True,
        description="Toggle automated posting of newly created ads to the designated channel",
    )
    AUTOPOST_COOLDOWN_SECONDS: int = Field(
        default=3,
        description="Minimum seconds between channel auto-posts to prevent flood",
    )
    AD_LIFETIME_DAYS: int = Field(
        default=7, description="Listing lifetime in days before auto-archive"
    )
    CHAT_IDLE_TIMEOUT_HOURS: int = Field(
        default=48, description="Chat idle timeout in hours before auto-close"
    )

    DB_POOL_SIZE: int = Field(default=20, description="SQLAlchemy asyncpg pool size")
    DB_MAX_OVERFLOW: int = Field(default=10, description="Max overflow connections")
    DB_POOL_TIMEOUT: int = Field(default=30, description="Pool timeout in seconds")
    DB_POOL_RECYCLE: int = Field(
        default=1800, description="Pool recycle timeout in seconds"
    )
    DB_ECHO: bool = Field(default=False, description="Echo SQL queries for debugging")

    ENVIRONMENT: str = Field(
        default="development",
        description="Deployment environment: 'development' or 'production'",
    )
    SESSION_SECRET_KEY: str = Field(
        default="standoff-super-secret-session-key-change-in-prod",
        description="Cryptographic secret used for signing admin session tokens",
    )
    WEB_ADMIN_SECURE_COOKIE: bool = Field(
        default=False,
        description="Enable Secure flag on cookies for HTTPS production deployments",
    )

    WEB_ADMIN_HOST: str = Field(
        default="127.0.0.1", description="Web admin panel bind host"
    )
    WEB_ADMIN_PORT: int = Field(default=8080, description="Web admin panel bind port")
    WEB_ADMIN_SECRET: str = Field(
        default="admin123",
        description="Master secret / password for web admin panel access",
    )
    BEHIND_PROXY: bool = Field(
        default=False,
        description="Trust X-Forwarded-For headers from reverse proxy",
    )

    @field_validator("WEB_ADMIN_SECRET")
    @classmethod
    def validate_web_admin_secret(cls, v: str, info: Any) -> str:
        """Prevent weak or default admin secrets in production."""
        env = "development"
        if hasattr(info, "data") and isinstance(info.data, dict):
            env = info.data.get("ENVIRONMENT", "development")
        if env == "production" and (len(v) < 16 or v in ("admin123", "", "admin")):
            raise ValueError(
                "In production environment, WEB_ADMIN_SECRET must be at least 16 characters long and secure!"
            )
        return v

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
