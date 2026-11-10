import asyncio
import logging
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
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.base import get_db_session
from src.database.models import Ad
from src.database.repositories.subscription_repo import SubscriptionRepository
from src.database.repositories.user_repo import UserRepository
from src.utils.formatters import format_ad_caption

logger = logging.getLogger(__name__)

PUSH_DELAY_SECONDS: float = 0.040

_background_notification_tasks: set[asyncio.Task[Any]] = set()


def get_push_notification_keyboard(ad_id: int) -> InlineKeyboardMarkup:
    """Build action keyboard for push notification message."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💬 Написать продавцу",
                    callback_data=f"chat_start:{ad_id}",
                    style="primary",
                )
            ]
        ]
    )


async def _send_subscriber_pushes(
    bot: Bot,
    ad_id: int,
    photo_file_id: str,
    caption: str,
    user_ids: list[int],
) -> int:
    """Worker delivering notifications sequentially to matching subscribers."""
    keyboard = get_push_notification_keyboard(ad_id)
    sent_count = 0
    deactivated_ids: list[int] = []

    for user_id in user_ids:
        try:
            await bot.send_photo(
                chat_id=user_id,
                photo=photo_file_id,
                caption=caption,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
            sent_count += 1
        except TelegramRetryAfter as e:
            logger.warning(
                "Push alert hit FloodWait! Sleeping for %d seconds...",
                e.retry_after,
            )
            await asyncio.sleep(e.retry_after + 1)
            try:
                await bot.send_photo(
                    chat_id=user_id,
                    photo=photo_file_id,
                    caption=caption,
                    reply_markup=keyboard,
                    parse_mode="HTML",
                )
                sent_count += 1
            except TelegramAPIError as retry_err:
                logger.error("Retry failed for push subscriber %d: %s", user_id, retry_err)
        except TelegramForbiddenError:
            logger.info("Subscriber %d blocked the bot; queuing deactivation.", user_id)
            deactivated_ids.append(user_id)
        except TelegramBadRequest as e:
            err_text = str(e).lower()
            if "chat not found" in err_text or "user not found" in err_text:
                deactivated_ids.append(user_id)
            else:
                logger.warning("Failed to send push to subscriber %d: %s", user_id, e)
        except TelegramAPIError as e:
            logger.error("Telegram API error sending push alert to %d: %s", user_id, e)

        await asyncio.sleep(PUSH_DELAY_SECONDS)

    if deactivated_ids:
        try:
            async with get_db_session() as db_sess:
                await UserRepository(db_sess).set_active_status_bulk(deactivated_ids, False)
        except SQLAlchemyError as exc:
            logger.warning("Failed to bulk mark deactivated subscribers: %s", exc)

    logger.info("Delivered %d push alerts for ad #%d", sent_count, ad_id)
    return sent_count


async def notify_subscribers_about_new_ad(
    bot: Bot,
    session: AsyncSession | None = None,
    ad: Ad | None = None,
    matching_user_ids: list[int] | None = None,
) -> int:
    """Find matching buyers and deliver instant push notifications about a new skin listing.

    Can be invoked with pre-queried user IDs or will query subscribers independently.
    """
    if ad is None:
        return 0

    target_ids = matching_user_ids
    if target_ids is None:
        if session is not None:
            sub_repo = SubscriptionRepository(session)
            target_ids = await sub_repo.find_matching_subscribers(ad)
        else:
            async with get_db_session() as db_session:
                sub_repo = SubscriptionRepository(db_session)
                target_ids = await sub_repo.find_matching_subscribers(ad)

    if not target_ids:
        return 0

    notification_text = format_ad_caption(ad, mode="push")

    return await _send_subscriber_pushes(
        bot=bot,
        ad_id=ad.id,
        photo_file_id=ad.photo_file_id,
        caption=notification_text,
        user_ids=target_ids,
    )
