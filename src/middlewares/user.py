import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, TelegramObject
from aiogram.types import User as TelegramUser
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.settings_manager import (
    format_main_menu_signature,
    get_main_menu_signature,
    get_menu_last_broadcast_sig,
)
from src.database.repositories.user_repo import UserRepository
from src.keyboards.main_menu import (
    BTN_BACK_MENU,
    BTN_BUY_SKIN,
    BTN_FEED_FILTER,
    BTN_FEED_MORE,
    BTN_HIDE_MENU,
    get_main_menu_keyboard,
)

logger = logging.getLogger(__name__)

_MAX_TRACKED_MENU_USERS: int = 20_000
_MENU_SELF_REFRESH_COMMANDS: frozenset[str] = frozenset(
    {"/start", "/menu", "/cancel", "/help"}
)
_MENU_SELF_REFRESH_TEXTS: frozenset[str] = frozenset(
    {
        BTN_BACK_MENU.casefold(),
        "отмена",
        "❌ отмена",
        "назад",
        "⬅️ назад",
        "главное меню",
        "/menu",
        "меню",
    }
)
_MENU_SKIP_TEXTS: frozenset[str] = frozenset(
    {BTN_BUY_SKIN, BTN_FEED_FILTER, BTN_FEED_MORE, BTN_HIDE_MENU}
)


class UserCheckMiddleware(BaseMiddleware):
    """Middleware for auto-registering users, refreshing profiles, enforcing bans, and syncing dynamic main menu."""

    def __init__(self) -> None:
        super().__init__()
        self._user_menu_sigs: dict[int, str] = {}

    def _record_user_sig(self, user_id: int, sig_str: str) -> None:
        """Remember the last main menu signature delivered to a user (bounded in memory)."""
        if len(self._user_menu_sigs) >= _MAX_TRACKED_MENU_USERS and user_id not in self._user_menu_sigs:
            self._user_menu_sigs.clear()
        self._user_menu_sigs[user_id] = sig_str

    async def _maybe_sync_user_menu(
        self,
        event: TelegramObject,
        user_id: int,
        data: dict[str, Any],
    ) -> None:
        """Silently refresh cached Telegram ReplyKeyboard when dynamic ad button state changed."""
        if not isinstance(event, Message):
            return
        if not event.chat or event.chat.type != "private":
            return

        current_sig = get_main_menu_signature()
        current_sig_str = format_main_menu_signature(current_sig)

        raw_text = (event.text or "").strip()
        first_token = (
            raw_text.split(maxsplit=1)[0].split("@", maxsplit=1)[0].lower()
            if raw_text
            else ""
        )

        # Handlers for these commands/buttons already deliver a fresh get_main_menu_keyboard(),
        # or user tapped the active custom button (meaning their client already has it).
        if (
            first_token in _MENU_SELF_REFRESH_COMMANDS
            or raw_text.casefold() in _MENU_SELF_REFRESH_TEXTS
            or (current_sig[0] and raw_text == current_sig[1])
        ):
            self._record_user_sig(user_id, current_sig_str)
            return

        # Never overwrite buyer-feed reply keyboard or unhide a manually hidden menu.
        if raw_text in _MENU_SKIP_TEXTS:
            return

        # Do not inject messages while user is inside an active FSM flow (chat/ad creation/report).
        state: FSMContext | None = data.get("state")
        if state is not None:
            try:
                if await state.get_state() is not None:
                    return
            except Exception:  # noqa: BLE001
                return

        prev_sig = self._user_menu_sigs.get(user_id)
        if prev_sig == current_sig_str:
            return

        broadcast_sig = get_menu_last_broadcast_sig()
        if broadcast_sig == current_sig_str:
            self._record_user_sig(user_id, current_sig_str)
            return
        if prev_sig is None and not current_sig[0]:
            self._record_user_sig(user_id, current_sig_str)
            return

        try:
            await event.answer(
                text="🔄 <b>Главное меню обновлено.</b>",
                reply_markup=get_main_menu_keyboard(),
                disable_notification=True,
            )
            self._record_user_sig(user_id, current_sig_str)
        except TelegramAPIError as exc:
            logger.debug("Failed to refresh main menu for user %d: %s", user_id, exc)

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

        await self._maybe_sync_user_menu(event=event, user_id=user.id, data=data)
        return await handler(event, data)
