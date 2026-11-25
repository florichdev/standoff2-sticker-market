import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from src.config import settings

logger = logging.getLogger(__name__)

SETTINGS_FILE = Path(__file__).resolve().parent / "market_settings.json"

DEFAULT_CUSTOM_AD_BUTTON: dict[str, Any] = {
    "enabled": False,
    "text": "Партнёр",
    "url": "https://example.com/",
    "message_text": "🔗 <b>Партнёрское предложение</b>\n\nДля перехода на сайт партнёра нажмите кнопку ниже 👇",
    "link_button_text": "🌐 Перейти на сайт",
}

_DEFAULT_SETTINGS: dict[str, Any] = {
    "max_active_ads": settings.MAX_ACTIVE_ADS,
    "stars_turbo_price": settings.STARS_TURBO_PRICE,
    "stars_standard_price": settings.STARS_STANDARD_PRICE,
    "autopost_channel_id": settings.AUTOPOST_CHANNEL_ID or "",
    "autopost_enabled": settings.AUTOPOST_ENABLED,
    "referrals_for_top": settings.DEFAULT_REFERRALS_FOR_TOP,
    "custom_ad_button": DEFAULT_CUSTOM_AD_BUTTON.copy(),
    "menu_last_broadcast_sig": "",
}

_cached_settings: dict[str, Any] | None = None
_last_settings_stat: tuple[int, int, int] | None = None


def _clone_settings(data: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of settings with isolated nested custom_ad_button dict."""
    cloned = data.copy()
    ad_btn = DEFAULT_CUSTOM_AD_BUTTON.copy()
    raw_btn = data.get("custom_ad_button")
    if isinstance(raw_btn, dict):
        ad_btn.update(raw_btn)
    cloned["custom_ad_button"] = ad_btn
    return cloned


def _parse_bool(val: Any, default: bool = False) -> bool:
    """Safely coerce bool/string values loaded from JSON settings."""
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in ("true", "1", "yes", "on")
    if val is None:
        return default
    return bool(val)


def _normalize_url(raw_url: Any) -> str:
    """Normalize sponsor URL so Telegram InlineKeyboardButton never rejects it."""
    url = str(raw_url or "").strip()
    if not url:
        return str(DEFAULT_CUSTOM_AD_BUTTON["url"])
    if url.startswith("@") and len(url) > 1:
        return f"https://t.me/{url.lstrip('@')}"
    if not url.startswith(("http://", "https://", "tg://")):
        url = f"https://{url}"
    return url


def _persist_settings_atomic(data: dict[str, Any]) -> None:
    """Persist settings JSON atomically with cross-process readable permissions."""
    global _cached_settings, _last_settings_stat
    _cached_settings = _clone_settings(data)

    temp_name: str | None = None
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", dir=str(SETTINGS_FILE.parent), delete=False, encoding="utf-8"
        ) as tf:
            json.dump(data, tf, ensure_ascii=False, indent=2)
            temp_name = tf.name

        try:
            os.chmod(temp_name, 0o644)
        except OSError:
            pass

        for attempt in range(5):
            try:
                os.replace(temp_name, SETTINGS_FILE)
                temp_name = None
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05)

        st = SETTINGS_FILE.stat()
        _last_settings_stat = (st.st_mtime_ns, st.st_ino, st.st_size)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to persist market settings to %s: %s", SETTINGS_FILE, exc)
        if temp_name and os.path.exists(temp_name):
            try:
                os.remove(temp_name)
            except OSError:
                pass


def get_market_settings() -> dict[str, Any]:
    """Retrieve runtime marketplace configuration with disk persistence fallback."""
    global _cached_settings, _last_settings_stat

    if SETTINGS_FILE.is_file():
        try:
            st = SETTINGS_FILE.stat()
            file_stat = (st.st_mtime_ns, st.st_ino, st.st_size)
            if _cached_settings is not None and file_stat == _last_settings_stat:
                return _clone_settings(_cached_settings)

            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    merged = _DEFAULT_SETTINGS.copy()
                    merged.update(data)
                    merged = _clone_settings(merged)
                    _cached_settings = merged
                    _last_settings_stat = file_stat
                    return _clone_settings(_cached_settings)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read %s, using defaults: %s", SETTINGS_FILE, exc)
            if _cached_settings is not None:
                return _clone_settings(_cached_settings)

    if _cached_settings is None:
        _cached_settings = _clone_settings(_DEFAULT_SETTINGS)
    return _clone_settings(_cached_settings)


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


def get_custom_ad_button() -> dict[str, Any]:
    """Return normalized runtime configuration for custom main menu sponsor button."""
    cfg = get_market_settings()
    val = cfg.get("custom_ad_button")
    res = DEFAULT_CUSTOM_AD_BUTTON.copy()
    if isinstance(val, dict):
        res.update(val)
    res["enabled"] = _parse_bool(res.get("enabled"), default=False)
    res["text"] = str(res.get("text") or "").strip()
    res["url"] = _normalize_url(res.get("url"))
    res["message_text"] = (
        str(res.get("message_text") or "").strip()
        or str(DEFAULT_CUSTOM_AD_BUTTON["message_text"])
    )
    res["link_button_text"] = (
        str(res.get("link_button_text") or "").strip()
        or str(DEFAULT_CUSTOM_AD_BUTTON["link_button_text"])
    )
    return res


def get_main_menu_signature() -> tuple[bool, str]:
    """Return compact fingerprint of current dynamic main menu button state."""
    btn = get_custom_ad_button()
    text = btn["text"]
    enabled = bool(btn["enabled"] and text)
    return (enabled, text if enabled else "")


def format_main_menu_signature(sig: tuple[bool, str]) -> str:
    """Serialize main menu signature to a stable string identifier."""
    return f"1:{sig[1]}" if sig[0] else "0:"


def get_menu_last_broadcast_sig() -> str:
    """Return the last main menu signature that was pushed to all active users."""
    cfg = get_market_settings()
    return str(cfg.get("menu_last_broadcast_sig") or "")


def set_menu_last_broadcast_sig(sig_str: str) -> None:
    """Record that all active users have been synced with the given menu signature."""
    current = get_market_settings()
    if str(current.get("menu_last_broadcast_sig") or "") == sig_str:
        return
    current["menu_last_broadcast_sig"] = sig_str
    _persist_settings_atomic(current)


def update_market_settings(
    max_active_ads: int | None = None,
    stars_turbo_price: int | None = None,
    stars_standard_price: int | None = None,
    autopost_channel_id: str | None = None,
    autopost_enabled: bool | None = None,
    referrals_for_top: int | None = None,
    custom_ad_button: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Update dynamic marketplace settings and persist to JSON file."""
    current = get_market_settings()
    old_menu_sig = get_main_menu_signature()

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
    if custom_ad_button is not None:
        btn_dict = get_custom_ad_button()
        if "enabled" in custom_ad_button:
            btn_dict["enabled"] = _parse_bool(custom_ad_button["enabled"])
        if "text" in custom_ad_button and custom_ad_button["text"] is not None:
            btn_dict["text"] = str(custom_ad_button["text"]).strip()
        if "url" in custom_ad_button and custom_ad_button["url"] is not None:
            btn_dict["url"] = _normalize_url(custom_ad_button["url"])
        if "message_text" in custom_ad_button and custom_ad_button["message_text"] is not None:
            btn_dict["message_text"] = str(custom_ad_button["message_text"]).strip()
        if "link_button_text" in custom_ad_button and custom_ad_button["link_button_text"] is not None:
            btn_dict["link_button_text"] = str(custom_ad_button["link_button_text"]).strip()
        current["custom_ad_button"] = btn_dict

        new_text = btn_dict["text"]
        new_menu_sig = (bool(btn_dict["enabled"] and new_text), new_text if (btn_dict["enabled"] and new_text) else "")
        if new_menu_sig != old_menu_sig:
            current["menu_last_broadcast_sig"] = ""

    _persist_settings_atomic(current)
    return _clone_settings(current)

