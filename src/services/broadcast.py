import asyncio
import logging
from datetime import UTC, datetime

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.database.base import get_db_session
from src.database.repositories.admin_repo import AdminRepository
from src.database.repositories.user_repo import UserRepository

logger = logging.getLogger(__name__)

import re

BROADCAST_DELAY_SECONDS: float = 0.040


def detect_parse_mode(text: str) -> str:
    """Detect whether text contains HTML tags or defaults to Markdown/HTML."""
    if not text:
        return "HTML"
    if re.search(
        r"<\/?(b|i|code|s|u|a|pre|strong|em|tg-spoiler)\b", text, re.IGNORECASE
    ):
        return "HTML"
    if re.search(r"(\*\*|__|`|\[.+\]\(.+\))", text):
        return "Markdown"
    return "HTML"


async def _send_broadcast_item(
    bot: Bot, user_id: int, text: str, photo_file_id: str | None
) -> None:
    """Send broadcast message handling photo caption length limit and markup format."""
    parse_mode: str | None = detect_parse_mode(text)
    try:
        if photo_file_id:
            if len(text) <= 1024:
                await bot.send_photo(
                    chat_id=user_id,
                    photo=photo_file_id,
                    caption=text,
                    parse_mode=parse_mode,
                )
            else:
                await bot.send_photo(
                    chat_id=user_id,
                    photo=photo_file_id,
                )
                await bot.send_message(
                    chat_id=user_id,
                    text=text,
                    parse_mode=parse_mode,
                )
        else:
            await bot.send_message(
                chat_id=user_id,
                text=text,
                parse_mode=parse_mode,
            )
    except TelegramBadRequest as exc:
        if "can't parse entities" in str(exc).lower():
            logger.warning(
                "Entity parsing failed for user %d with %s; falling back to plain text",
                user_id,
                parse_mode,
            )
            if photo_file_id:
                if len(text) <= 1024:
                    await bot.send_photo(
                        chat_id=user_id,
                        photo=photo_file_id,
                        caption=text,
                        parse_mode=None,
                    )
                else:
                    await bot.send_photo(
                        chat_id=user_id,
                        photo=photo_file_id,
                    )
                    await bot.send_message(
                        chat_id=user_id,
                        text=text,
                        parse_mode=None,
                    )
            else:
                await bot.send_message(
                    chat_id=user_id,
                    text=text,
                    parse_mode=None,
                )
        else:
            raise


async def broadcast_worker(
    bot: Bot,
    session_maker: async_sessionmaker[AsyncSession],
    admin_id: int,
    text: str,
    photo_file_id: str | None = None,
) -> tuple[int, int, int]:
    """Asynchronous background worker for mass messaging with FloodWait protection."""
    start_time = datetime.now(UTC)

    async with session_maker() as session:
        admin_repo = AdminRepository(session)
        user_ids = await admin_repo.get_all_active_user_ids()

    total_recipients = len(user_ids)
    success_count = 0
    blocked_count = 0
    error_count = 0
    deactivated_ids: set[int] = set()

    logger.info(
        "Started mass broadcast to %d users requested by admin %d",
        total_recipients,
        admin_id,
    )

    for user_id in user_ids:
        try:
            await _send_broadcast_item(bot, user_id, text, photo_file_id)
            success_count += 1
        except TelegramRetryAfter as e:
            logger.warning(
                "Broadcast hit FloodWait! Sleeping for %d seconds...",
                e.retry_after,
            )
            await asyncio.sleep(e.retry_after + 1)
            try:
                await _send_broadcast_item(bot, user_id, text, photo_file_id)
                success_count += 1
            except TelegramAPIError as retry_err:
                logger.error("Retry failed for user %d: %s", user_id, retry_err)
                error_count += 1
        except TelegramForbiddenError:
            blocked_count += 1
            deactivated_ids.add(user_id)
        except TelegramBadRequest as e:
            if (
                "chat not found" in str(e).lower()
                or "user is deactivated" in str(e).lower()
            ):
                blocked_count += 1
                deactivated_ids.add(user_id)
            else:
                logger.warning("Bad request sending to %d: %s", user_id, e)
                error_count += 1
        except TelegramAPIError as e:
            logger.warning("Telegram API error sending to %d: %s", user_id, e)
            error_count += 1

        if len(deactivated_ids) >= 50:
            try:
                async with get_db_session() as session:
                    await UserRepository(session).set_active_status_bulk(deactivated_ids, False)
                deactivated_ids.clear()
            except SQLAlchemyError as err:
                logger.warning("Could not batch deactivate users: %s", err)

        await asyncio.sleep(BROADCAST_DELAY_SECONDS)

    if deactivated_ids:
        try:
            async with get_db_session() as session:
                await UserRepository(session).set_active_status_bulk(deactivated_ids, False)
        except SQLAlchemyError as err:
            logger.warning("Could not final batch deactivate users: %s", err)

    elapsed_seconds = round((datetime.now(UTC) - start_time).total_seconds(), 1)
    summary_text = (
        "📊 <b>Отчет о массовой рассылке</b>\n\n"
        f"👥 Всего получателей: <b>{total_recipients}</b>\n"
        f"✅ Успешно доставлено: <b>{success_count}</b>\n"
        f"🚫 Заблокировали бота: <b>{blocked_count}</b>\n"
        f"⚠️ Ошибок доставки: <b>{error_count}</b>\n"
        f"⏱ Время выполнения: <b>{elapsed_seconds} сек.</b>"
    )

    if admin_id > 0:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=summary_text,
                parse_mode="HTML",
            )
        except TelegramAPIError as e:
            logger.error(
                "Failed to send broadcast summary report to admin %d: %s",
                admin_id,
                e,
            )

    return success_count, blocked_count, error_count


async def sync_active_users_on_startup(bot: Bot) -> None:
    """Log active user count on bot startup without sending API spam.

    User deactivation is handled reactively on TelegramForbiddenError/BadRequest during broadcasts.
    """
    logger.info("Verifying active users in database on bot startup...")
    try:
        async with get_db_session() as session:
            admin_repo = AdminRepository(session)
            user_ids = await admin_repo.get_all_active_user_ids()
        logger.info("Active users in database on startup: %d users.", len(user_ids))
    except SQLAlchemyError as e:
        logger.warning("Could not query active users on startup: %s", e)
