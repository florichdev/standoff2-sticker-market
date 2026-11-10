import asyncio
from html import escape
import logging
import time
from typing import Any

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.exc import SQLAlchemyError

from src.config import settings
from src.data.settings_manager import get_autopost_channel_id, is_autopost_enabled
from src.database.base import async_session_maker
from src.database.models import Ad
from src.database.repositories.admin_repo import AdminRepository
from src.database.repositories.user_repo import UserRepository
from src.utils.formatters import format_sticker_label
from src.utils.helpers import safe_send_photo

logger = logging.getLogger(__name__)

# Per-seller cooldown tracking (seller_id -> last_timestamp)
_seller_last_autopost: dict[int, float] = {}
SELLER_COOLDOWN_SECONDS: float = 5.0

# In-memory deduplication set (up to 5000 IDs)
_posted_ad_ids: set[int] = set()

# Set to retain background task references against Python GC
_background_autopost_tasks: set[asyncio.Task[Any]] = set()


def build_channel_autopost_caption(ad: Ad, seller_name: str | None = None) -> str:
    """Format rich notification caption for an automated listing post in Telegram channel using valid HTML."""
    sticker_info = format_sticker_label(
        ad.sticker_count, ad.sticker_rarity, bold=True
    )
    top_badge = " 🔥 <b>ТОП-ПРЕДЛОЖЕНИЕ</b>" if ad.is_top else ""

    name = seller_name
    if not name and hasattr(ad, "seller") and ad.seller:
        name = ad.seller.first_name
    raw_name = str(name).strip() if name and str(name).strip() else "Продавец"
    display_name = escape(raw_name)
    item_title = f"{escape(ad.weapon_model)} | {escape(ad.skin_name)}"

    price_formatted = f"{ad.price_gold:,}".replace(",", " ")
    lines = [
        f"🔔 <b>Новое объявление на маркете!</b>{top_badge}",
        "",
        f"🔫 <b>Предмет:</b> <code>{item_title}</code>",
        f"📁 <b>Категория:</b> {escape(ad.category)}",
        f"🏷️ <b>Наклейки:</b> {sticker_info}",
        f"💰 <b>Цена:</b> <b>{price_formatted} G</b>",
        "",
        f"👤 <b>Продавец:</b> {display_name}",
        "🛡️ <i>Безопасная P2P-сделка через бота</i>",
    ]
    return "\n".join(lines)


# Backward-compatible alias
build_inbot_autopost_caption = build_channel_autopost_caption


def build_channel_autopost_keyboard(bot_username: str, ad_id: int) -> InlineKeyboardMarkup:
    """Build URL button directing channel subscribers to lot view in bot."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🛒 Открыть лот в боте",
                    url=f"https://t.me/{bot_username}?start=ad_{ad_id}",
                )
            ]
        ]
    )


def build_inbot_autopost_keyboard(ad_id: int) -> InlineKeyboardMarkup:
    """Backward compatibility alias for in-bot button."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💬 Написать продавцу",
                    callback_data=f"chat_start:{ad_id}",
                )
            ]
        ]
    )


build_inbot_ad_keyboard = build_inbot_autopost_keyboard


async def _execute_channel_autopost(
    bot: Bot,
    ad_id: int,
    photo_file_id: str,
    caption: str,
    channel_id: str,
    delay: float = 0.0,
) -> bool:
    """Background worker posting listing exclusively to designated channel."""
    if delay > 0:
        await asyncio.sleep(delay)
    try:
        bot_user = await bot.get_me()
        bot_username = bot_user.username or "Standstickerbot"
        keyboard = build_channel_autopost_keyboard(bot_username=bot_username, ad_id=ad_id)

        msg = await safe_send_photo(
            bot=bot,
            chat_id=channel_id,
            photo=photo_file_id,
            caption=caption,
            reply_markup=keyboard,
            parse_mode="HTML",
            ad_id=ad_id,
        )
        if msg is not None:
            logger.info("[AUTOPOST] Successfully posted ad #%d to channel %s", ad_id, channel_id)
            return True
        logger.warning(
            "[AUTOPOST] safe_send_photo returned None for ad #%d to channel %s",
            ad_id,
            channel_id,
        )
        return False
    except TelegramForbiddenError as exc:
        logger.error(
            "[AUTOPOST] Bot lacks rights to post in channel %s: %s. "
            "Ensure the bot is added as Administrator with Post Messages rights.",
            channel_id,
            exc,
        )
        return False
    except TelegramBadRequest as exc:
        logger.error(
            "[AUTOPOST] TelegramBadRequest posting ad #%d to channel %s: %s. "
            "Check that channel ID or @username is correct.",
            ad_id,
            channel_id,
            exc,
        )
        return False
    except TelegramAPIError as exc:
        logger.error("[AUTOPOST] Telegram API error posting ad #%d to channel %s: %s", ad_id, channel_id, exc)
        return False
    except Exception as exc:
        logger.exception("[AUTOPOST] Unexpected error posting ad #%d to channel %s: %s", ad_id, channel_id, exc)
        return False


async def _execute_inbot_autopost(
    bot: Bot,
    ad_id: int,
    photo_file_id: str,
    caption: str,
    skip_user_ids: set[int] | None = None,
    delay: float = 0.0,
) -> int:
    """Deliver newly published listing to active bot users with FloodWait protection."""
    if delay > 0:
        await asyncio.sleep(delay)

    skip = set(skip_user_ids) if skip_user_ids else set()

    try:
        async with async_session_maker() as session:
            admin_repo = AdminRepository(session)
            user_ids = await admin_repo.get_all_active_user_ids()
    except Exception as exc:
        logger.error("[AUTOPOST] Failed querying active users for in-bot autopost: %s", exc)
        return 0

    recipients = [uid for uid in user_ids if uid not in skip]
    if not recipients:
        logger.info("[AUTOPOST] No eligible recipients for in-bot autopost of ad #%d", ad_id)
        return 0

    keyboard = build_inbot_autopost_keyboard(ad_id)
    sent_count = 0
    deactivated_ids: set[int] = set()

    for uid in recipients:
        try:
            msg = await safe_send_photo(
                bot=bot,
                chat_id=uid,
                photo=photo_file_id,
                caption=caption,
                reply_markup=keyboard,
                parse_mode="HTML",
                ad_id=ad_id,
            )
            if msg is not None:
                sent_count += 1
        except TelegramRetryAfter as e:
            logger.warning("[AUTOPOST] Hit FloodWait for user %d! Sleeping for %d seconds...", uid, e.retry_after)
            await asyncio.sleep(e.retry_after + 1)
            try:
                msg = await safe_send_photo(
                    bot=bot,
                    chat_id=uid,
                    photo=photo_file_id,
                    caption=caption,
                    reply_markup=keyboard,
                    parse_mode="HTML",
                    ad_id=ad_id,
                )
                if msg is not None:
                    sent_count += 1
            except TelegramAPIError as retry_err:
                logger.error("[AUTOPOST] Retry failed for user %d: %s", uid, retry_err)
        except TelegramForbiddenError:
            deactivated_ids.add(uid)
        except TelegramBadRequest as e:
            err_msg = str(e).lower()
            if "chat not found" in err_msg or "user is deactivated" in err_msg:
                deactivated_ids.add(uid)
            else:
                logger.warning("[AUTOPOST] BadRequest sending ad #%d to user %d: %s", ad_id, uid, e)
        except TelegramAPIError as e:
            logger.warning("[AUTOPOST] Telegram API error sending ad #%d to user %d: %s", ad_id, uid, e)

        if len(deactivated_ids) >= 50:
            try:
                async with async_session_maker() as session:
                    await UserRepository(session).set_active_status_bulk(deactivated_ids, False)
                deactivated_ids.clear()
            except SQLAlchemyError:
                pass

        await asyncio.sleep(0.040)

    if deactivated_ids:
        try:
            async with async_session_maker() as session:
                await UserRepository(session).set_active_status_bulk(deactivated_ids, False)
        except SQLAlchemyError:
            pass

    logger.info("[AUTOPOST] Delivered in-bot ad #%d to %d users", ad_id, sent_count)
    return sent_count


async def autopost_new_ad(
    bot: Bot,
    ad: Ad,
    skip_user_ids: set[int] | None = None,
    seller_name: str | None = None,
) -> bool:
    """Trigger automated posting of a newly published listing:
    1. To the public Telegram channel (if AUTOPOST_CHANNEL_ID is configured).
    2. Directly inside the bot to active users (if autopost_enabled is True).

    Runs in tracked background tasks without blocking the seller conversation flow.
    """
    logger.info("[AUTOPOST] Received trigger for ad #%d (seller_id=%d)", ad.id, ad.seller_id)

    if not is_autopost_enabled():
        logger.info(
            "[AUTOPOST] Autopost is disabled in settings. Skipping autopost for ad #%d.",
            ad.id,
        )
        return False

    # Deduplication guard
    if ad.id in _posted_ad_ids:
        logger.info("[AUTOPOST] Ad #%d already autoposted; skipping duplicate post.", ad.id)
        return False

    # Anti-flood rate-limiting per seller using configurable cooldown
    cooldown = float(getattr(settings, "AUTOPOST_COOLDOWN_SECONDS", 3))
    now = time.monotonic()
    seller_last = _seller_last_autopost.get(ad.seller_id, 0.0)
    delay = 0.0
    if (now - seller_last) < cooldown:
        delay = cooldown - (now - seller_last)
        logger.info(
            "[AUTOPOST] Seller ID %d created ad #%d within %.1fs cooldown. Delaying autopost by %.1fs.",
            ad.seller_id,
            ad.id,
            cooldown,
            delay,
        )

    _seller_last_autopost[ad.seller_id] = now + delay
    _posted_ad_ids.add(ad.id)
    if len(_posted_ad_ids) > 5000:
        _posted_ad_ids.clear()

    caption = build_channel_autopost_caption(ad, seller_name=seller_name)

    # 1. Post to Telegram channel if configured
    channel_id = get_autopost_channel_id()
    if channel_id and str(channel_id).strip():
        target_channel = str(channel_id).strip()
        channel_task = asyncio.create_task(
            _execute_channel_autopost(
                bot=bot,
                ad_id=ad.id,
                photo_file_id=ad.photo_file_id,
                caption=caption,
                channel_id=target_channel,
                delay=delay,
            )
        )
        _background_autopost_tasks.add(channel_task)
        channel_task.add_done_callback(_background_autopost_tasks.discard)
    else:
        logger.info(
            "[AUTOPOST] No AUTOPOST_CHANNEL_ID configured in settings. Skipping channel post for ad #%d.",
            ad.id,
        )

    # 2. In-bot broadcast to active users
    skip = set(skip_user_ids) if skip_user_ids else set()
    skip.add(ad.seller_id)  # Do not notify the seller about their own lot

    inbot_task = asyncio.create_task(
        _execute_inbot_autopost(
            bot=bot,
            ad_id=ad.id,
            photo_file_id=ad.photo_file_id,
            caption=caption,
            skip_user_ids=skip,
            delay=delay,
        )
    )
    _background_autopost_tasks.add(inbot_task)
    inbot_task.add_done_callback(_background_autopost_tasks.discard)

    return True
