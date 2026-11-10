import asyncio
import logging
import sys
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters.chat_member_updated import (
    ADMINISTRATOR,
    KICKED,
    LEFT,
    MEMBER,
    ChatMemberUpdatedFilter,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from aiogram.types import ChatMemberUpdated, ErrorEvent
from apscheduler.schedulers.asyncio import (  # type: ignore[import-untyped]
    AsyncIOScheduler,
)
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.database.base import async_session_maker, close_db
from src.database.repositories.user_repo import UserRepository
from src.handlers import admin, buyer, chat, profile, report, seller, start
from src.middlewares.db import DbSessionMiddleware
from src.middlewares.user import UserCheckMiddleware
from src.services.broadcast import sync_active_users_on_startup
from src.services.logger_service import setup_app_logging
from src.services.scheduler import setup_scheduler

logger = logging.getLogger(__name__)

scheduler: AsyncIOScheduler | None = None
_background_tasks: set[asyncio.Task[Any]] = set()


def setup_logging() -> None:
    """Configure unified application logging with daily rotating file handler."""
    setup_app_logging(logging.INFO)


async def on_startup(bot: Bot) -> None:
    """Execute startup routines and start background scheduler."""
    global scheduler
    bot_info = await bot.get_me()
    logger.info(
        "Bot started successfully: @%s (ID: %d)", bot_info.username, bot_info.id
    )
    scheduler = setup_scheduler(bot=bot, session_maker=async_session_maker)
    scheduler.start()
    logger.info("APScheduler background jobs started successfully.")

    task = asyncio.create_task(sync_active_users_on_startup(bot=bot))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def on_shutdown() -> None:
    """Execute graceful shutdown routines."""
    if scheduler is not None and scheduler.running:
        logger.info("Stopping APScheduler background jobs...")
        scheduler.shutdown(wait=False)

    for task in list(_background_tasks):
        if not task.done():
            task.cancel()
    if _background_tasks:
        await asyncio.gather(*_background_tasks, return_exceptions=True)

    logger.info("Shutting down bot and closing database connections...")
    await close_db()
    logger.info("Shutdown completed.")


async def global_error_handler(
    event: ErrorEvent,
    state: FSMContext | None = None,
) -> bool:
    """Catch unhandled exceptions, reset broken FSM state and notify user."""
    logger.exception(
        "Unhandled exception while processing update: %s",
        event.exception,
    )

    if state is not None:
        try:
            await state.clear()
        except Exception as storage_exc:  # noqa: BLE001
            logger.debug("Failed to reset FSM state on error: %s", storage_exc)

    if event.update:
        if event.update.callback_query:
            try:
                await event.update.callback_query.answer(
                    text="⚠️ Произошла ошибка. Пожалуйста, попробуйте позже.",
                    show_alert=True,
                )
            except TelegramAPIError as exc:
                logger.debug("Failed to deliver alert to user: %s", exc)
        elif event.update.message:
            try:
                await event.update.message.answer(
                    "⚠️ Произошла внутренняя ошибка. Попробуйте повторить действие позже или перезапустите бота командой /start."
                )
            except TelegramAPIError as exc:
                logger.debug("Failed to deliver message to user: %s", exc)
    return True


def create_storage() -> Any:
    """Instantiate RedisStorage if REDIS_URL is configured, else fallback to MemoryStorage."""
    if settings.REDIS_URL:
        try:
            from aiogram.fsm.storage.redis import RedisStorage

            storage = RedisStorage.from_url(settings.REDIS_URL)
            logger.info("Using RedisStorage for FSM persistence: %s", settings.REDIS_URL)
            return storage
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to initialize RedisStorage (%s); falling back to MemoryStorage",
                exc,
            )
    return MemoryStorage()


def create_dispatcher(storage: Any | None = None) -> Dispatcher:
    """Create and configure Dispatcher with middlewares, handlers and lifecycle hooks."""
    fsm_storage = storage or create_storage()
    dp = Dispatcher(storage=fsm_storage, events_isolation=SimpleEventIsolation())

    dp.error.register(global_error_handler)
    dp.update.outer_middleware(DbSessionMiddleware(session_pool=async_session_maker))

    user_middleware = UserCheckMiddleware()
    dp.message.middleware(user_middleware)
    dp.callback_query.middleware(user_middleware)

    @dp.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=KICKED | LEFT))
    async def handle_user_blocked_bot(
        event: ChatMemberUpdated, session: AsyncSession
    ) -> None:
        if event.chat.type != "private":
            return
        user_repo = UserRepository(session)
        await user_repo.set_active_status(event.from_user.id, False)
        logger.info("User %d blocked the bot; marked as inactive.", event.from_user.id)

    @dp.my_chat_member(
        ChatMemberUpdatedFilter(member_status_changed=MEMBER | ADMINISTRATOR)
    )
    async def handle_user_unblocked_bot(
        event: ChatMemberUpdated, session: AsyncSession
    ) -> None:
        if event.chat.type != "private":
            return
        user_repo = UserRepository(session)
        await user_repo.set_active_status(event.from_user.id, True)
        logger.info("User %d unblocked the bot; marked as active.", event.from_user.id)

    dp.include_router(start.router)
    dp.include_router(chat.router)
    dp.include_router(profile.router)
    dp.include_router(report.router)
    dp.include_router(admin.router)
    dp.include_router(buyer.router)
    dp.include_router(seller.router)

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    return dp


async def main() -> None:
    """Main application entry point."""
    setup_logging()

    if not settings.BOT_TOKEN:
        logger.error("BOT_TOKEN is not configured! Please check your .env file.")
        sys.exit(1)

    bot_session = None
    if settings.TELEGRAM_PROXY:
        from aiogram.client.session.aiohttp import AiohttpSession
        bot_session = AiohttpSession(proxy=settings.TELEGRAM_PROXY)
        logger.info("Connecting to Telegram API via proxy: %s", settings.TELEGRAM_PROXY)

    bot = Bot(
        token=settings.BOT_TOKEN,
        session=bot_session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = create_dispatcher()

    logger.info("Starting polling...")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        await dp.storage.close()
        await close_db()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot process terminated.")
    except Exception as exc:
        logger.critical("Fatal error running Telegram bot: %s", exc)
