import html
import logging
from datetime import UTC, datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
)
from apscheduler.schedulers.asyncio import (  # type: ignore[import-untyped]
    AsyncIOScheduler,
)
from apscheduler.triggers.interval import (  # type: ignore[import-untyped]
    IntervalTrigger,
)
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import settings
from src.database.models import Ad, ChatMessage, ChatSession
from src.services.backup_service import perform_database_backup

logger = logging.getLogger(__name__)




async def clean_expired_top_ads(
    session_maker: async_sessionmaker[AsyncSession],
) -> int:
    """Demote ads whose TOP promotion period has expired (runs every 15 mins)."""
    now = datetime.now(UTC)
    async with session_maker() as session:
        statement = (
            update(Ad)
            .where(
                Ad.is_top.is_(True),
                Ad.top_until.is_not(None),
                Ad.top_until < now,
            )
            .values(is_top=False, top_until=None)
            .returning(Ad.id)
        )
        result = await session.execute(statement)
        demoted_ids = list(result.scalars().all())
        await session.commit()

    if demoted_ids:
        logger.info("Demoted %d expired TOP ads: %s", len(demoted_ids), demoted_ids)
    return len(demoted_ids)


async def auto_archive_stale_ads(
    bot: Bot,
    session_maker: async_sessionmaker[AsyncSession],
) -> int:
    """Deactivate ads published more than AD_LIFETIME_DAYS ago and notify sellers (runs every 1 hour)."""
    cutoff = datetime.now(UTC) - timedelta(days=settings.AD_LIFETIME_DAYS)
    archived_ads: list[tuple[int, int, str, str]] = []

    async with session_maker() as session:
        select_stmt = select(Ad.id, Ad.seller_id, Ad.weapon_model, Ad.skin_name).where(
            Ad.is_active.is_(True),
            Ad.created_at < cutoff,
        )
        result = await session.execute(select_stmt)
        for row in result.all():
            archived_ads.append((row[0], row[1], row[2], row[3]))

        if archived_ads:
            ad_ids = [item[0] for item in archived_ads]
            update_stmt = update(Ad).where(Ad.id.in_(ad_ids)).values(is_active=False)
            await session.execute(update_stmt)
            await session.commit()

    if not archived_ads:
        return 0

    logger.info("Auto-archived %d stale listings (>%d days)", len(archived_ads), settings.AD_LIFETIME_DAYS)

    for ad_id, seller_id, weapon, skin in archived_ads:
        safe_wpn = html.escape(weapon or "Оружие")
        safe_skn = html.escape(skin or "Скин")
        try:
            await bot.send_message(
                chat_id=seller_id,
                text=(
                    "📦 <b>Срок публикации объявления истек</b>\n\n"
                    f"Ваш лот #{ad_id} (<b>{safe_wpn} | {safe_skn}</b>) был автоматически "
                    f"снят с продажи, так как находился в ленте более {settings.AD_LIFETIME_DAYS} дней.\n\n"
                    "Вы можете выставить его заново в любой момент через кнопку 💰 <b>Продать скин</b>."
                ),
                parse_mode="HTML",
            )
        except TelegramForbiddenError:
            logger.debug(
                "Seller %d blocked the bot; skipping archive notice.", seller_id
            )
        except (TelegramBadRequest, TelegramAPIError) as e:
            logger.warning(
                "Failed to send archive notice to seller %d for ad #%d: %s",
                seller_id,
                ad_id,
                e,
            )

    return len(archived_ads)


async def auto_close_abandoned_chats(
    bot: Bot,
    session_maker: async_sessionmaker[AsyncSession],
) -> int:
    """Close active chat sessions with no messages for >CHAT_IDLE_TIMEOUT_HOURS (runs every 30 mins)."""
    cutoff = datetime.now(UTC) - timedelta(hours=settings.CHAT_IDLE_TIMEOUT_HOURS)
    now = datetime.now(UTC)
    abandoned_chats: list[tuple[int, int, int]] = []

    async with session_maker() as session:
        subquery = (
            select(
                ChatMessage.chat_id,
                func.max(ChatMessage.sent_at).label("last_msg_at"),
            )
            .group_by(ChatMessage.chat_id)
            .subquery()
        )

        statement = (
            select(ChatSession.id, ChatSession.buyer_id, ChatSession.seller_id)
            .outerjoin(subquery, ChatSession.id == subquery.c.chat_id)
            .where(
                ChatSession.is_active.is_(True),
                func.coalesce(subquery.c.last_msg_at, ChatSession.created_at) < cutoff,
            )
        )
        result = await session.execute(statement)
        for row in result.all():
            abandoned_chats.append((row[0], row[1], row[2]))

        if abandoned_chats:
            chat_ids = [item[0] for item in abandoned_chats]
            update_stmt = (
                update(ChatSession)
                .where(ChatSession.id.in_(chat_ids))
                .values(is_active=False, closed_at=now)
            )
            await session.execute(update_stmt)
            await session.commit()

    if not abandoned_chats:
        return 0

    logger.info(
        "Auto-closed %d abandoned chat sessions (>%dh idle)",
        len(abandoned_chats),
        settings.CHAT_IDLE_TIMEOUT_HOURS,
    )

    notice_text = (
        "⏳ <b>Диалог закрыт по неактивности</b>\n\n"
        f"В этом чате не было сообщений более {settings.CHAT_IDLE_TIMEOUT_HOURS} часов. Сессия была автоматически завершена."
    )

    for _session_id, buyer_id, seller_id in abandoned_chats:
        for user_id in (buyer_id, seller_id):
            try:
                await bot.send_message(
                    chat_id=user_id,
                    text=notice_text,
                    parse_mode="HTML",
                )
            except TelegramForbiddenError:
                logger.debug("User %d blocked bot, skip chat close notice", user_id)
            except (TelegramBadRequest, TelegramAPIError) as e:
                logger.warning(
                    "Failed to send abandoned chat notice to user %d: %s",
                    user_id,
                    e,
                )

    return len(abandoned_chats)




def setup_scheduler(
    bot: Bot,
    session_maker: async_sessionmaker[AsyncSession],
) -> AsyncIOScheduler:
    """Instantiate and configure AsyncIOScheduler with maintenance jobs."""
    job_defaults = {
        "coalesce": True,
        "max_instances": 1,
        "misfire_grace_time": 60,
    }
    scheduler = AsyncIOScheduler(timezone=UTC, job_defaults=job_defaults)

    scheduler.add_job(
        clean_expired_top_ads,
        trigger=IntervalTrigger(minutes=15),
        args=[session_maker],
        id="clean_expired_top_ads",
        replace_existing=True,
    )

    scheduler.add_job(
        auto_archive_stale_ads,
        trigger=IntervalTrigger(hours=1),
        args=[bot, session_maker],
        id="auto_archive_stale_ads",
        replace_existing=True,
    )

    scheduler.add_job(
        auto_close_abandoned_chats,
        trigger=IntervalTrigger(minutes=30),
        args=[bot, session_maker],
        id="auto_close_abandoned_chats",
        replace_existing=True,
    )

    scheduler.add_job(
        perform_database_backup,
        trigger=IntervalTrigger(hours=24),
        id="daily_database_backup",
        replace_existing=True,
    )

    return scheduler
