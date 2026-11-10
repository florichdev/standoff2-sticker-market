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
from src.database.repositories.ad_repo import AdRepository
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

# Registry of active background tasks per ad_id for instant cancellation upon deletion/ban
_active_ad_tasks: dict[int, set[asyncio.Task[Any]]] = {}


def register_ad_task(ad_id: int, task: asyncio.Task[Any]) -> None:
    """Register an ongoing asynchronous task associated with an ad to allow immediate cancellation."""
    if ad_id not in _active_ad_tasks:
        _active_ad_tasks[ad_id] = set()
    _active_ad_tasks[ad_id].add(task)

    def _cleanup(_: Any) -> None:
        tasks = _active_ad_tasks.get(ad_id)
        if tasks:
            tasks.discard(task)
            if not tasks:
                _active_ad_tasks.pop(ad_id, None)

    task.add_done_callback(_cleanup)


def cancel_ad_tasks(ad_id: int) -> None:
    """Immediately cancel all ongoing background tasks (channel autopost, in-bot broadcast, push) for an ad."""
    tasks = _active_ad_tasks.pop(ad_id, set())
    for t in tasks:
        if not t.done():
            t.cancel()



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
    try:
        if delay > 0:
            await asyncio.sleep(delay)
            # Check if ad is still active before posting
            try:
                async with async_session_maker() as session:
                    ad = await AdRepository(session).get_by_id(ad_id)
                    if ad is not None and not ad.is_active:
                        logger.info("[AUTOPOST] Ad #%d inactive or deleted before channel post. Aborting.", ad_id)
                        return False
            except Exception as exc:
                logger.warning("[AUTOPOST] Error verifying ad status before channel post: %s", exc)

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
            try:
                async with async_session_maker() as session:
                    ad_repo = AdRepository(session)
                    # Double check if ad was deactivated concurrently while sending
                    curr_ad = await ad_repo.get_by_id(ad_id)
                    if curr_ad is None or not curr_ad.is_active:
                        try:
                            await bot.delete_message(chat_id=msg.chat.id, message_id=msg.message_id)
                        except Exception:
                            pass
                        return False
                    await ad_repo.save_broadcast_messages(ad_id, [(msg.chat.id, msg.message_id)])
            except Exception as exc:
                logger.warning("[AUTOPOST] Failed saving channel post record for ad #%d: %s", ad_id, exc)
            return True
        logger.warning(
            "[AUTOPOST] safe_send_photo returned None for ad #%d to channel %s",
            ad_id,
            channel_id,
        )
        return False
    except asyncio.CancelledError:
        logger.info("[AUTOPOST] Channel autopost task for ad #%d was cancelled.", ad_id)
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
    """Deliver newly published listing to active bot users with FloodWait protection and high throughput."""
    pending_records: list[tuple[int, int]] = []
    db_lock = asyncio.Lock()

    async def _flush_records() -> None:
        nonlocal pending_records
        async with db_lock:
            if not pending_records:
                return
            to_save = list(pending_records)
            pending_records.clear()
        try:
            async with async_session_maker() as session:
                await AdRepository(session).save_broadcast_messages(ad_id, to_save)
        except Exception as exc:
            logger.warning("[AUTOPOST] Failed saving %d broadcast records: %s", len(to_save), exc)

    try:
        if delay > 0:
            await asyncio.sleep(delay)
            # Check if ad is still active before broadcasting
            try:
                async with async_session_maker() as session:
                    ad = await AdRepository(session).get_by_id(ad_id)
                    if ad is not None and not ad.is_active:
                        logger.info("[AUTOPOST] Ad #%d inactive or deleted before in-bot broadcast. Aborting.", ad_id)
                        return 0
            except Exception as exc:
                logger.warning("[AUTOPOST] Error verifying ad status before in-bot broadcast: %s", exc)

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

        # Prioritize delivery: platform admins first, then newest registered accounts (descending ID)
        admin_set = set(settings.ADMIN_IDS)
        recipients.sort(key=lambda uid: (0 if uid in admin_set else 1, -uid))

        keyboard = build_inbot_autopost_keyboard(ad_id)
        sent_count = 0
        deactivated_ids: set[int] = set()
        sem = asyncio.Semaphore(5)
        stop_broadcasting = False

        async def _send_to_user(uid: int) -> None:
            nonlocal sent_count, stop_broadcasting
            if stop_broadcasting:
                return

            async with sem:
                if stop_broadcasting:
                    return
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
                        async with db_lock:
                            pending_records.append((msg.chat.id, msg.message_id))
                            should_flush = len(pending_records) >= 10
                        if should_flush:
                            await _flush_records()
                except TelegramRetryAfter as e:
                    logger.warning("[AUTOPOST] Hit FloodWait for user %d: sleeping %ds", uid, e.retry_after)
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
                            async with db_lock:
                                pending_records.append((msg.chat.id, msg.message_id))
                    except Exception:
                        pass
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

                await asyncio.sleep(0.035)

        logger.info("[AUTOPOST] Starting in-bot delivery of ad #%d to %d users", ad_id, len(recipients))

        chunk_size = 10
        for i in range(0, len(recipients), chunk_size):
            if i > 0 and i % 30 == 0:
                try:
                    async with async_session_maker() as session:
                        ad = await AdRepository(session).get_by_id(ad_id)
                        if ad is not None and not ad.is_active:
                            logger.info(
                                "[AUTOPOST] Ad #%d deactivated mid-broadcast. Terminating early at %d/%d.",
                                ad_id,
                                sent_count,
                                len(recipients),
                            )
                            stop_broadcasting = True
                            break
                except Exception:
                    pass

            chunk = recipients[i : i + chunk_size]
            await asyncio.gather(*[_send_to_user(uid) for uid in chunk])
            if stop_broadcasting:
                break

            if len(deactivated_ids) >= 50:
                try:
                    async with async_session_maker() as session:
                        await UserRepository(session).set_active_status_bulk(deactivated_ids, False)
                    deactivated_ids.clear()
                except SQLAlchemyError:
                    pass

        await _flush_records()

        if deactivated_ids:
            try:
                async with async_session_maker() as session:
                    await UserRepository(session).set_active_status_bulk(deactivated_ids, False)
            except SQLAlchemyError:
                pass

        logger.info("[AUTOPOST] Delivered in-bot ad #%d to %d users", ad_id, sent_count)
        return sent_count
    except asyncio.CancelledError:
        logger.info("[AUTOPOST] In-bot broadcast task for ad #%d was cancelled.", ad_id)
        await _flush_records()
        return 0


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
        register_ad_task(ad.id, channel_task)
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
    register_ad_task(ad.id, inbot_task)

    return True


async def delete_ad_broadcast_messages(
    bot: Bot,
    ad_ids: list[int] | int | None = None,
    direct_messages: list[tuple[int, int]] | None = None,
) -> int:
    """Asynchronously delete all broadcast & push messages sent for the given ad(s) across Telegram chats and channels."""
    clean_ids: list[int] = []
    if ad_ids is not None:
        if isinstance(ad_ids, int):
            clean_ids = [ad_ids]
        else:
            clean_ids = list(ad_ids)

    # 1. Immediately cancel any ongoing background tasks for these ads
    if clean_ids:
        for aid in clean_ids:
            cancel_ad_tasks(aid)
        await asyncio.sleep(0.050)

    messages: list[tuple[int, int]] = []
    if direct_messages:
        messages.extend(direct_messages)

    if clean_ids:
        try:
            async with async_session_maker() as session:
                ad_repo = AdRepository(session)
                db_messages = await ad_repo.get_broadcast_messages_for_ads(clean_ids)
                messages.extend(db_messages)
        except Exception as exc:
            logger.error("[AUTOPOST_CLEANUP] Failed fetching message records for ads %s: %s", clean_ids, exc)

    if not messages:
        logger.info("[AUTOPOST_CLEANUP] No broadcast messages found to delete for ads %s", clean_ids or direct_messages)
        return 0

    unique_messages = list(dict.fromkeys(messages))
    deleted_count = 0
    for chat_id, message_id in unique_messages:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
            deleted_count += 1
        except TelegramRetryAfter as e:
            logger.warning("[AUTOPOST_CLEANUP] FloodWait deleting message %d in chat %d: sleeping %d s", message_id, chat_id, e.retry_after)
            await asyncio.sleep(e.retry_after + 1)
            try:
                await bot.delete_message(chat_id=chat_id, message_id=message_id)
                deleted_count += 1
            except Exception:
                pass
        except (TelegramBadRequest, TelegramForbiddenError) as e:
            logger.debug("[AUTOPOST_CLEANUP] Could not delete message %d in chat %d: %s", message_id, chat_id, e)
        except Exception as e:
            logger.warning("[AUTOPOST_CLEANUP] Unexpected error deleting message %d in chat %d: %s", message_id, chat_id, e)

        await asyncio.sleep(0.030)

    # 2. Check for any trailing messages saved right before cancellation and purge records
    if clean_ids:
        try:
            async with async_session_maker() as session:
                ad_repo = AdRepository(session)
                trailing_msgs = await ad_repo.get_broadcast_messages_for_ads(clean_ids)
                if trailing_msgs:
                    for t_chat_id, t_msg_id in trailing_msgs:
                        if (t_chat_id, t_msg_id) not in unique_messages:
                            try:
                                await bot.delete_message(chat_id=t_chat_id, message_id=t_msg_id)
                                deleted_count += 1
                            except Exception:
                                pass
                            await asyncio.sleep(0.030)
                await ad_repo.delete_broadcast_message_records(clean_ids)
        except Exception as exc:
            logger.error("[AUTOPOST_CLEANUP] Failed purging broadcast message records for ads %s: %s", clean_ids, exc)

    logger.info("[AUTOPOST_CLEANUP] Deleted %d/%d broadcast messages for ads %s", deleted_count, len(unique_messages), clean_ids or direct_messages)
    return deleted_count


def schedule_ad_broadcast_cleanup(
    bot: Bot | None,
    ad_ids: list[int] | int | None = None,
    direct_messages: list[tuple[int, int]] | None = None,
) -> None:
    """Schedule asynchronous deletion of broadcast messages in background."""
    if not bot:
        try:
            from src.web.app import get_bot_instance

            bot = get_bot_instance()
        except ImportError:
            bot = None
    if not bot:
        logger.warning("[AUTOPOST_CLEANUP] No bot instance available to clean broadcast messages")
        return

    task = asyncio.create_task(
        delete_ad_broadcast_messages(bot, ad_ids=ad_ids, direct_messages=direct_messages)
    )
    _background_autopost_tasks.add(task)
    task.add_done_callback(_background_autopost_tasks.discard)
