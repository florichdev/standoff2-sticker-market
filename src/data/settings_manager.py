import json
import logging
from pathlib import Path
from typing import Any

from src.config import settings

logger = logging.getLogger(__name__)

SETTINGS_FILE = Path(__file__).resolve().parent / "market_settings.json"

_DEFAULT_SETTINGS: dict[str, Any] = {
    "max_active_ads": settings.MAX_ACTIVE_ADS,
    "stars_turbo_price": settings.STARS_TURBO_PRICE,
    "stars_standard_price": settings.STARS_STANDARD_PRICE,
    "autopost_channel_id": settings.AUTOPOST_CHANNEL_ID or "",
    "autopost_enabled": settings.AUTOPOST_ENABLED,
    "referrals_for_top": settings.DEFAULT_REFERRALS_FOR_TOP,
}

_cached_settings: dict[str, Any] | None = None
_last_settings_mtime: float = -1.0


def get_market_settings() -> dict[str, Any]:
    """Retrieve runtime marketplace configuration with disk persistence fallback."""
    global _cached_settings, _last_settings_mtime

    if SETTINGS_FILE.is_file():
        try:
            mtime = SETTINGS_FILE.stat().st_mtime
            if _cached_settings is not None and mtime == _last_settings_mtime:
                return _cached_settings.copy()

            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    merged = _DEFAULT_SETTINGS.copy()
                    merged.update(data)
                    _cached_settings = merged
                    _last_settings_mtime = mtime
                    return _cached_settings.copy()
        except Exception as exc:
            logger.warning("Could not read %s, using defaults: %s", SETTINGS_FILE, exc)
            if _cached_settings is not None:
                return _cached_settings.copy()

    if _cached_settings is None:
        _cached_settings = _DEFAULT_SETTINGS.copy()
    return _cached_settings.copy()


def get_max_active_ads() -> int:
    """Return maximum allowed active ads per seller."""
    cfg = get_market_settings()
    val = cfg.get("max_active_ads", settings.MAX_ACTIVE_ADS)
    try:
        return max(1, int(val))
    except (ValueError, TypeError):
        return 10


def get_stars_turbo_price() -> int:
    """Return dynamic price in Telegram Stars for Turbo promotion."""
    cfg = get_market_settings()
    val = cfg.get("stars_turbo_price", settings.STARS_TURBO_PRICE)
    try:
        return max(1, int(val))
    except (ValueError, TypeError):
        return 150


def get_stars_standard_price() -> int:
    """Return dynamic price in Telegram Stars for Standard promotion."""
    cfg = get_market_settings()
    val = cfg.get("stars_standard_price", settings.STARS_STANDARD_PRICE)
    try:
        return max(1, int(val))
    except (ValueError, TypeError):
        return 50


def get_referrals_for_top() -> int:
    """Return number of referred friends required to earn 1 free TOP boost (1-50)."""
    cfg = get_market_settings()
    val = cfg.get("referrals_for_top", settings.DEFAULT_REFERRALS_FOR_TOP)
    try:
        return max(1, min(50, int(val)))
    except (ValueError, TypeError):
        return 3


def get_autopost_channel_id() -> str | None:
    """Return configured Telegram channel username or ID for live listings feed."""
    cfg = get_market_settings()
    val = cfg.get("autopost_channel_id")
    if val and str(val).strip():
        return str(val).strip()
    return settings.AUTOPOST_CHANNEL_ID


def is_autopost_enabled() -> bool:
    """Check if automated in-bot broadcast of new listings to Telegram users is active."""
    cfg = get_market_settings()
    val = cfg.get("autopost_enabled")
    if val is not None:
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.lower() in ("true", "1", "yes", "on")
        return bool(val)
    return getattr(settings, "AUTOPOST_ENABLED", True)


def update_market_settings(
    max_active_ads: int | None = None,
    stars_turbo_price: int | None = None,
    stars_standard_price: int | None = None,
    autopost_channel_id: str | None = None,
    autopost_enabled: bool | None = None,
    referrals_for_top: int | None = None,
) -> dict[str, Any]:
    """Update dynamic marketplace settings and persist to JSON file."""
    global _cached_settings, _last_settings_mtime
    current = get_market_settings()

    if max_active_ads is not None:
        current["max_active_ads"] = max(1, int(max_active_ads))
    if stars_turbo_price is not None:
        current["stars_turbo_price"] = max(1, int(stars_turbo_price))
    if stars_standard_price is not None:
        current["stars_standard_price"] = max(1, int(stars_standard_price))
    if autopost_channel_id is not None:
        current["autopost_channel_id"] = autopost_channel_id.strip()
    if autopost_enabled is not None:
        current["autopost_enabled"] = bool(autopost_enabled)
    if referrals_for_top is not None:
        current["referrals_for_top"] = max(1, min(50, int(referrals_for_top)))

    _cached_settings = current.copy()

    try:
        import os
        import tempfile

        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp_name = None
        with tempfile.NamedTemporaryFile(
            "w", dir=str(SETTINGS_FILE.parent), delete=False, encoding="utf-8"
        ) as tf:
            json.dump(current, tf, ensure_ascii=False, indent=2)
            temp_name = tf.name
        os.replace(temp_name, SETTINGS_FILE)
        _last_settings_mtime = SETTINGS_FILE.stat().st_mtime
    except Exception as exc:
        logger.error("Failed to persist market settings to %s: %s", SETTINGS_FILE, exc)

    return current.copy()

