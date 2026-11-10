from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from aiogram.types import User as TelegramUser
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.repositories.user_repo import UserRepository


class UserCheckMiddleware(BaseMiddleware):
    """Middleware for auto-registering users, refreshing profiles, and enforcing bans."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        session: AsyncSession | None = data.get("session")
        if session is None:
            return await handler(event, data)

        event_user: TelegramUser | None = data.get("event_from_user")
        if event_user is None or event_user.is_bot:
            return await handler(event, data)

        user_repo = UserRepository(session)
        user, is_created = await user_repo.get_or_create(
            user_id=event_user.id,
            username=event_user.username,
            first_name=event_user.first_name,
        )

        if user.is_banned:
            if isinstance(event, Message):
                await event.answer("⛔ Ваш аккаунт заблокирован администрацией.")
            elif isinstance(event, CallbackQuery):
                await event.answer("⛔ Ваш аккаунт заблокирован.", show_alert=True)
            return None

        data["user"] = user
        data["user_repo"] = user_repo
        data["is_new_user"] = is_created
        return await handler(event, data)
