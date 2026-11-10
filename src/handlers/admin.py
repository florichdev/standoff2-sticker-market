import asyncio
import logging
from typing import Any

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import BaseFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.database.base import async_session_maker
from src.database.models import User
from src.database.repositories.ad_repo import AdRepository
from src.database.repositories.admin_repo import AdminRepository
from src.keyboards.admin import (
    CB_ADMIN_BAN,
    CB_ADMIN_BROADCAST,
    CB_ADMIN_DEL_AD,
    CB_ADMIN_DISMISS,
    CB_ADMIN_HISTORY,
    CB_ADMIN_HISTORY_VIEW,
    CB_ADMIN_LOG,
    CB_ADMIN_MENU,
    CB_ADMIN_REPORT_VIEW,
    CB_ADMIN_REPORTS,
    CB_ADMIN_RESOLVE,
    CB_ADMIN_SEARCH,
    CB_ADMIN_UNBAN,
    CB_ADMIN_VIEW_AD,
    CB_BROADCAST_CANCEL,
    CB_BROADCAST_CONFIRM,
    get_admin_main_keyboard,
    get_broadcast_confirmation_keyboard,
    get_report_action_keyboard,
    get_reports_history_keyboard,
    get_reports_list_keyboard,
    get_user_action_keyboard,
)
from src.services.autopost import schedule_ad_broadcast_cleanup
from src.services.broadcast import broadcast_worker, detect_parse_mode
from src.states.admin import (
    AdminUserSearchStates,
    BroadcastStates,
)
from src.utils.formatters import format_ad_caption
from src.utils.helpers import safe_answer_photo, safe_edit_message

logger = logging.getLogger(__name__)
router = Router(name="admin")
_background_tasks: set[asyncio.Task[Any]] = set()


class IsAdmin(BaseFilter):
    """Filter granting access exclusively to configured admin Telegram IDs."""

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user = event.from_user
        return user is not None and user.id in settings.ADMIN_IDS


async def safe_edit_text(
    message: Message,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str = "Markdown",
) -> None:
    """Safely edit a message or its caption, delegating to unified safe_edit_message."""
    await safe_edit_message(
        target=message,
        text=text,
        reply_markup=reply_markup,
        parse_mode=parse_mode,
    )




@router.message(Command("admin"), IsAdmin())
async def show_admin_dashboard(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    """Display system metrics and administrative menu."""
    await state.clear()
    admin_repo = AdminRepository(session)
    stats = await admin_repo.get_system_stats()
    pending_count, history_count = await admin_repo.count_reports_by_status()

    text = (
        "👑 **Панель администратора Standsticker**\n\n"
        f"👥 Всего пользователей: **{stats['users_total']}** (заблокировано: {stats['users_banned']})\n"
        f"📦 Активных лотов: **{stats['ads_active']}** (ТОП: {stats['ads_top']})\n"
        f"💬 Открытых чатов: **{stats['chats_active']}**\n"
        f"📋 Ожидают проверки жалоб: **{pending_count}**\n"
        f"📜 Рассмотрено в архиве: **{history_count}**\n\n"
        "Выберите раздел для управления:"
    )

    await message.answer(
        text=text,
        reply_markup=get_admin_main_keyboard(pending_count, history_count),
        parse_mode="Markdown",
    )


@router.callback_query(F.data == CB_ADMIN_MENU, IsAdmin())
async def callback_admin_dashboard(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    """Refresh admin dashboard."""
    admin_repo = AdminRepository(session)
    stats = await admin_repo.get_system_stats()
    pending_count, history_count = await admin_repo.count_reports_by_status()

    text = (
        "👑 **Панель администратора Standsticker**\n\n"
        f"👥 Всего пользователей: **{stats['users_total']}** (заблокировано: {stats['users_banned']})\n"
        f"📦 Активных лотов: **{stats['ads_active']}** (ТОП: {stats['ads_top']})\n"
        f"💬 Открытых чатов: **{stats['chats_active']}**\n"
        f"📋 Ожидают проверки жалоб: **{pending_count}**\n"
        f"📜 Рассмотрено в архиве: **{history_count}**\n\n"
        "Выберите раздел для управления:"
    )

    if isinstance(callback.message, Message):
        await safe_edit_text(
            message=callback.message,
            text=text,
            reply_markup=get_admin_main_keyboard(pending_count, history_count),
        )
    await callback.answer()




@router.callback_query(F.data.startswith(CB_ADMIN_REPORTS), IsAdmin())
async def show_pending_reports(callback: CallbackQuery, session: AsyncSession) -> None:
    """Display paginated list of pending complaints."""
    try:
        offset = int(callback.data[len(CB_ADMIN_REPORTS) :])  # type: ignore[index]
    except (IndexError, ValueError):
        offset = 0

    admin_repo = AdminRepository(session)
    reports, total = await admin_repo.get_pending_reports(limit=5, offset=offset)

    if not reports:
        if isinstance(callback.message, Message):
            await safe_edit_text(
                message=callback.message,
                text="🎉 **Все жалобы рассмотрены!**\n\nОчередь модерации чиста.",
                reply_markup=get_admin_main_keyboard(0),
            )
        await callback.answer()
        return

    slice_data = [(r.id, r.violator_id, r.reason) for r in reports]
    text = (
        f"📋 **Очередь жалоб (Всего: {total}, показаны {offset + 1}-{offset + len(reports)}):**\n\n"
        "Нажмите на жалобу для просмотра деталей и принятия мер:"
    )

    if isinstance(callback.message, Message):
        await safe_edit_text(
            message=callback.message,
            text=text,
            reply_markup=get_reports_list_keyboard(slice_data, offset, total),
        )
    await callback.answer()


@router.callback_query(F.data.startswith(CB_ADMIN_REPORT_VIEW), IsAdmin())
async def view_single_report(callback: CallbackQuery, session: AsyncSession) -> None:
    """View detailed information about a single complaint."""
    try:
        report_id = int(callback.data[len(CB_ADMIN_REPORT_VIEW) :])  # type: ignore[index]
    except (IndexError, ValueError):
        await callback.answer("Ошибка параметров", show_alert=True)
        return

    admin_repo = AdminRepository(session)
    report = await admin_repo.get_report_by_id(report_id)

    if report is None:
        await callback.answer("Жалоба не найдена.", show_alert=True)
        return

    date_str = report.created_at.strftime("%d.%m.%Y %H:%M UTC")
    text = (
        f"📋 **Жалоба #{report.id}** ({report.status})\n\n"
        f"• **Заявитель:** `{report.reporter_id}`\n"
        f"• **Нарушитель:** `{report.violator_id}`\n"
        f"• **Лот:** `#{report.ad_id or '—'}`\n"
        f"• **Чат:** `#{report.chat_id or '—'}`\n"
        f"• **Дата подачи:** {date_str}\n\n"
        f"📝 **Причина:** {report.reason}"
    )

    if isinstance(callback.message, Message):
        await safe_edit_text(
            message=callback.message,
            text=text,
            reply_markup=get_report_action_keyboard(
                report_id=report.id,
                violator_id=report.violator_id,
                chat_id=report.chat_id,
                ad_id=report.ad_id,
            ),
        )
    await callback.answer()


@router.callback_query(F.data.startswith(CB_ADMIN_RESOLVE), IsAdmin())
async def resolve_report_handler(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    """Accept and mark complaint as resolved."""
    try:
        report_id = int(callback.data[len(CB_ADMIN_RESOLVE) :])  # type: ignore[index]
    except (IndexError, ValueError):
        await callback.answer("Ошибка параметров", show_alert=True)
        return

    admin_repo = AdminRepository(session)
    report = await admin_repo.get_report_by_id(report_id)
    if report is None:
        await callback.answer("Жалоба не найдена", show_alert=True)
        return

    ad_deactivated = False
    if report.ad_id:
        ad = await AdRepository(session).get_by_id(report.ad_id)
        if ad and ad.seller_id == report.violator_id and ad.is_active:
            ad_deactivated = await admin_repo.deactivate_ad_as_admin(report.ad_id)
            if ad_deactivated and callback.bot:
                schedule_ad_broadcast_cleanup(callback.bot, [report.ad_id])

    await admin_repo.resolve_report(report_id, status="resolved")

    if callback.bot and report.violator_id not in settings.ADMIN_IDS:
        try:
            lot_info = f" по лоту #{report.ad_id}" if (report.ad_id and ad_deactivated) else ""
            context_info = f" в диалоге сделки #{report.chat_id}" if (report.chat_id and not ad_deactivated) else ""
            warning_msg = (
                "⚠️ **Официальное предупреждение от администрации**\n\n"
                f"На вас поступила обоснованная жалоба{lot_info or context_info}.\n"
                f"{'Связанное объявление было автоматически снято с продажи.\n' if ad_deactivated else ''}"
                "Пожалуйста, соблюдайте правила честной торговли на маркете.\n\n"
                "❗️ **Внимание:** при повторных нарушениях или попытках мошенничества ваш профиль будет заблокирован навсегда."
            )
            await callback.bot.send_message(
                chat_id=report.violator_id,
                text=warning_msg,
                parse_mode="Markdown",
            )
        except TelegramAPIError as exc:
            logger.debug("Failed to deliver warning to violator %d: %s", report.violator_id, exc)

    if callback.bot and report.reporter_id:
        try:
            reporter_msg = (
                "✅ **Ваша жалоба была рассмотрена**\n\n"
                f"Администрация проверила жалобу #{report_id} и приняла меры: "
                f"нарушителю вынесено предупреждение{', а объявление снято с публикации' if ad_deactivated else ''}.\n\n"
                "Спасибо за содействие безопасности маркета!"
            )
            await callback.bot.send_message(
                chat_id=report.reporter_id,
                text=reporter_msg,
                parse_mode="Markdown",
            )
        except TelegramAPIError as exc:
            logger.debug("Failed to deliver notice to reporter %d: %s", report.reporter_id, exc)

    pending_cnt, hist_cnt = await admin_repo.count_reports_by_status()
    lot_action_desc = "Снят с продажи (принадлежал нарушителю)" if ad_deactivated else (
        "Не снимался (лот принадлежит заявителю / уже снят)" if report.ad_id else "Не был привязан"
    )
    status_text = (
        f"✅ **Жалоба #{report_id} принята.**\n\n"
        f"• Статус жалобы: Решена\n"
        f"• Лот: {lot_action_desc}\n"
        f"• Санкция: Нарушителю ID `{report.violator_id}` отправлено официальное предупреждение."
    )

    if isinstance(callback.message, Message):
        await safe_edit_text(
            message=callback.message,
            text=status_text,
            reply_markup=get_admin_main_keyboard(pending_cnt, hist_cnt),
        )
    await callback.answer("Жалоба принята, санкция наложена")


@router.callback_query(F.data.startswith(CB_ADMIN_DISMISS), IsAdmin())
async def dismiss_report(callback: CallbackQuery, session: AsyncSession) -> None:
    """Dismiss complaint as unfounded."""
    try:
        report_id = int(callback.data[len(CB_ADMIN_DISMISS) :])  # type: ignore[index]
    except (IndexError, ValueError):
        await callback.answer("Ошибка параметров", show_alert=True)
        return

    admin_repo = AdminRepository(session)
    await admin_repo.resolve_report(report_id, status="dismissed")

    if isinstance(callback.message, Message):
        await safe_edit_text(
            message=callback.message,
            text=f"❌ **Жалоба #{report_id} отклонена.**",
            reply_markup=get_admin_main_keyboard(0),
        )
    await callback.answer("Жалоба отклонена")


@router.callback_query(F.data.startswith(CB_ADMIN_VIEW_AD), IsAdmin())
async def view_ad_from_report(callback: CallbackQuery, session: AsyncSession) -> None:
    """View the reported lot card directly."""
    parts = callback.data[len(CB_ADMIN_VIEW_AD) :].split(":")  # type: ignore[index]
    try:
        ad_id = int(parts[0])
        report_id = int(parts[1]) if len(parts) > 1 else 0
    except (IndexError, ValueError):
        await callback.answer("Ошибка параметров", show_alert=True)
        return

    ad_repo = AdRepository(session)
    ad = await ad_repo.get_by_id(ad_id)
    if ad is None:
        await callback.answer("Лот не найден в базе данных.", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    if ad.is_active:
        builder.button(
            text="🗑 Снять лот с продажи",
            callback_data=f"{CB_ADMIN_DEL_AD}{ad.id}:{report_id}",
        )
    if report_id:
        builder.button(
            text="⬅️ Назад к жалобе",
            callback_data=f"{CB_ADMIN_REPORT_VIEW}{report_id}",
        )
    builder.adjust(1)

    caption = format_ad_caption(ad, mode="admin")

    if callback.message and isinstance(callback.message, Message):
        await safe_answer_photo(
            message=callback.message,
            photo=ad.photo_file_id,
            caption=caption,
            reply_markup=builder.as_markup(),
            parse_mode="Markdown",
        )
    await callback.answer()


@router.callback_query(F.data.startswith(CB_ADMIN_DEL_AD), IsAdmin())
async def delete_ad_from_report(callback: CallbackQuery, session: AsyncSession) -> None:
    """Deactivate offending lot directly from moderation view."""
    parts = callback.data[len(CB_ADMIN_DEL_AD) :].split(":")  # type: ignore[index]
    try:
        ad_id = int(parts[0])
    except (IndexError, ValueError):
        await callback.answer("Ошибка параметров", show_alert=True)
        return

    admin_repo = AdminRepository(session)
    success = await admin_repo.deactivate_ad_as_admin(ad_id)

    if success:
        if callback.bot:
            schedule_ad_broadcast_cleanup(callback.bot, [ad_id])
        await callback.answer(
            f"🗑 Лот #{ad_id} успешно снят с продажи!", show_alert=True
        )
        if isinstance(callback.message, Message) and callback.message.photo:
            await callback.message.edit_caption(
                caption=f"🗑 **Лот #{ad_id} снят с продажи администрацией.**",
                reply_markup=None,
                parse_mode="Markdown",
            )
    else:
        await callback.answer("Лот уже неактивен или не найден.", show_alert=True)


@router.callback_query(F.data.startswith(CB_ADMIN_BAN), IsAdmin())
async def execute_cascade_ban(callback: CallbackQuery, session: AsyncSession) -> None:
    """Execute 1-click cascade ban: ban user, drop ads, and close active chats."""
    if not callback.data:
        await callback.answer()
        return
    parts = callback.data.split(":")
    if len(parts) < 2:
        await callback.answer("Ошибка параметров", show_alert=True)
        return
    try:
        violator_id = int(parts[1])
        report_id = int(parts[2]) if len(parts) > 2 else 0
    except (IndexError, ValueError):
        await callback.answer("Ошибка параметров", show_alert=True)
        return

    if violator_id in settings.ADMIN_IDS or (
        callback.from_user and violator_id == callback.from_user.id
    ):
        await callback.answer(
            "⛔ Нельзя заблокировать администратора маркета!", show_alert=True
        )
        return

    admin_repo = AdminRepository(session)
    is_banned, ads_count, partners, deactivated_ads = await admin_repo.ban_user_cascade(violator_id)

    if not is_banned:
        await callback.answer("Пользователь не найден.", show_alert=True)
        return

    if report_id > 0:
        await admin_repo.resolve_report(report_id, status="resolved")

    if callback.bot:
        if deactivated_ads:
            schedule_ad_broadcast_cleanup(callback.bot, deactivated_ads)
        try:
            await callback.bot.send_message(
                chat_id=violator_id,
                text="🚫 **Ваш аккаунт был заблокирован за нарушение правил маркета.**",
                parse_mode="Markdown",
            )
        except TelegramAPIError as e:
            logger.debug("Could not deliver ban notice to %d: %s", violator_id, e)

        for partner_id in partners:
            try:
                await callback.bot.send_message(
                    chat_id=partner_id,
                    text=(
                        "⚠️ **Диалог принудительно закрыт.**\n"
                        "Собеседник был заблокирован администрацией за нарушение правил."
                    ),
                    parse_mode="Markdown",
                )
            except TelegramAPIError as e:
                logger.debug(
                    "Could not deliver chat close notice to partner %d: %s",
                    partner_id,
                    e,
                )

    result_text = (
        f"🔨 **Пользователь `{violator_id}` успешно забанен!**\n\n"
        f"• Деактивировано активных лотов: **{ads_count}**\n"
        f"• Закрыто активных диалогов: **{len(partners)}**"
    )

    if isinstance(callback.message, Message):
        await safe_edit_text(
            message=callback.message,
            text=result_text,
            reply_markup=get_admin_main_keyboard(0),
        )
    await callback.answer("Пользователь забанен!")


@router.callback_query(F.data.startswith(CB_ADMIN_LOG), IsAdmin())
async def view_chat_log(callback: CallbackQuery, session: AsyncSession) -> None:
    """Dump chat log messages for audit and evidence review."""
    if not callback.data:
        await callback.answer()
        return
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Ошибка параметров", show_alert=True)
        return
    try:
        chat_id = int(parts[1])
        _ = int(parts[2])
    except (IndexError, ValueError):
        await callback.answer("Ошибка параметров", show_alert=True)
        return

    admin_repo = AdminRepository(session)
    messages = await admin_repo.get_chat_logs(chat_id)

    if not messages:
        await callback.answer("Лог сообщений пуст.", show_alert=True)
        return

    lines = [f"📜 История сообщений чата #{chat_id}:\n"]
    for msg in messages:
        time_str = msg.sent_at.strftime("%H:%M:%S") if msg.sent_at else "--:--:--"
        content = (
            msg.text_content if msg.content_type == "text" else f"[{msg.content_type}]"
        )
        lines.append(f"[{time_str}] {msg.sender_id}: {content}")

    log_text = "\n".join(lines)
    if len(log_text) > 4000:
        log_text = log_text[:3980] + "\n...(сокращено)"

    if isinstance(callback.message, Message):
        await callback.message.reply(
            text=log_text,
            parse_mode=None,
        )
    await callback.answer()




@router.callback_query(F.data == CB_ADMIN_SEARCH, IsAdmin())
async def prompt_user_search(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt admin for Telegram user ID or @username."""
    await state.set_state(AdminUserSearchStates.waiting_for_user_id)
    text = (
        "🔍 **Поиск пользователя**\n\n"
        "Отправьте числовой Telegram ID или никнейм (@username) пользователя:"
    )
    if isinstance(callback.message, Message):
        await safe_edit_text(
            message=callback.message,
            text=text,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅️ Назад в меню", callback_data=CB_ADMIN_MENU
                        )
                    ]
                ]
            ),
        )
    await callback.answer()


@router.message(AdminUserSearchStates.waiting_for_user_id, IsAdmin())
async def process_user_search(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    """Look up user profile by numeric ID or @username."""
    query = (message.text or "").strip()
    if not query:
        await message.answer("⚠️ Пожалуйста, введите числовой ID или @username:")
        return

    admin_repo = AdminRepository(session)
    target_user = await admin_repo.find_user_by_id_or_username(query)

    if target_user is None:
        await message.answer(
            f"❌ Пользователь «`{query}`» не найден в базе данных маркета.\n\n"
            "Убедитесь, что пользователь хотя бы раз запускал бота (/start).",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🔍 Повторить поиск", callback_data=CB_ADMIN_SEARCH
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="⬅️ В панель управления", callback_data=CB_ADMIN_MENU
                        )
                    ],
                ]
            ),
            parse_mode="Markdown",
        )
        return

    await state.clear()
    reg_date = target_user.registration_date.strftime("%d.%m.%Y")
    ban_status = "🚫 **Заблокирован**" if target_user.is_banned else "✅ **Активен**"
    role_badge = (
        "👑 **[Администратор платформы]**\n"
        if target_user.id in settings.ADMIN_IDS
        else ""
    )

    profile_text = (
        f"{role_badge}"
        f"👤 **Карточка пользователя**\n\n"
        f"• **Telegram ID:** `{target_user.id}`\n"
        f"• **Никнейм:** @{target_user.username or '—'}\n"
        f"• **Имя:** {target_user.first_name or '—'}\n"
        f"• **Дата регистрации:** {reg_date}\n"
        f"• **Статус:** {ban_status}\n"
        f"• **Рейтинг:** {target_user.rating:.1f} ⭐️ ({target_user.ratings_count} отзывов)"
    )

    await message.answer(
        text=profile_text,
        reply_markup=get_user_action_keyboard(target_user.id, target_user.is_banned),
        parse_mode="Markdown",
    )


@router.callback_query(F.data == "noop_admin_shield", IsAdmin())
async def callback_admin_shield(callback: CallbackQuery) -> None:
    """Alert that admin accounts cannot be banned."""
    await callback.answer(
        "👑 Этот пользователь является администратором. Блокировка запрещена правилами безопасности.",
        show_alert=True,
    )


@router.callback_query(F.data.startswith(CB_ADMIN_UNBAN), IsAdmin())
async def execute_unban(callback: CallbackQuery, session: AsyncSession) -> None:
    """Unban a previously banned user."""
    try:
        user_id = int(callback.data[len(CB_ADMIN_UNBAN) :])  # type: ignore[index]
    except (IndexError, ValueError):
        await callback.answer("Ошибка параметров", show_alert=True)
        return

    admin_repo = AdminRepository(session)
    await admin_repo.unban_user(user_id)

    if isinstance(callback.message, Message):
        await safe_edit_text(
            message=callback.message,
            text=f"🤝 **Пользователь `{user_id}` успешно разбанен!**",
            reply_markup=get_admin_main_keyboard(0),
        )
    await callback.answer("Пользователь разбанен")




@router.callback_query(F.data == CB_ADMIN_BROADCAST, IsAdmin())
async def prompt_broadcast_content(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt admin for broadcast text or photo."""
    await state.set_state(BroadcastStates.waiting_for_content)
    text = (
        "📢 **Массовая рассылка сообщений**\n\n"
        "Отправьте текст сообщения или фотографию с подписью, которую получат все активные пользователи бота.\n\n"
        "Для отмены нажмите кнопку ниже 👇"
    )
    if isinstance(callback.message, Message):
        await safe_edit_text(
            message=callback.message,
            text=text,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅️ Назад в меню", callback_data=CB_ADMIN_MENU
                        )
                    ]
                ]
            ),
        )
    await callback.answer()


@router.message(BroadcastStates.waiting_for_content, IsAdmin())
async def preview_broadcast_content(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    """Display broadcast preview and confirm sending with caption length protection."""
    photo_file_id = message.photo[-1].file_id if message.photo else None
    text_content = (message.caption if message.photo else message.text) or ""

    if not text_content and not photo_file_id:
        await message.answer("⚠️ Сообщение не может быть пустым.")
        return

    admin_repo = AdminRepository(session)
    recipients = await admin_repo.get_all_active_user_ids()

    await state.update_data(
        broadcast_text=text_content,
        photo_file_id=photo_file_id,
        recipients_count=len(recipients),
    )
    await state.set_state(BroadcastStates.waiting_for_confirmation)

    mode = detect_parse_mode(text_content)
    if mode == "HTML":
        preview_header = (
            f"📢 <b>Предпросмотр рассылки</b> (Получателей: <b>{len(recipients)}</b>)\n\n"
        )
    else:
        preview_header = (
            f"📢 **Предпросмотр рассылки** (Получателей: **{len(recipients)}**)\n\n"
        )

    try:
        if photo_file_id:
            full_caption = f"{preview_header}{text_content}"
            if len(full_caption) <= 1024:
                await message.answer_photo(
                    photo=photo_file_id,
                    caption=full_caption,
                    reply_markup=get_broadcast_confirmation_keyboard(),
                    parse_mode=mode,
                )
            else:
                await message.answer_photo(
                    photo=photo_file_id,
                    caption=preview_header,
                    parse_mode=mode,
                )
                await message.answer(
                    text=text_content,
                    reply_markup=get_broadcast_confirmation_keyboard(),
                    parse_mode=mode,
                )
        else:
            await message.answer(
                text=f"{preview_header}{text_content}",
                reply_markup=get_broadcast_confirmation_keyboard(),
                parse_mode=mode,
            )
    except TelegramBadRequest as exc:
        if "can't parse entities" in str(exc).lower():
            if photo_file_id:
                await message.answer_photo(
                    photo=photo_file_id,
                    caption=f"📢 Предпросмотр рассылки (Получателей: {len(recipients)})\n\n{text_content}"[:1024],
                    reply_markup=get_broadcast_confirmation_keyboard(),
                    parse_mode=None,
                )
            else:
                await message.answer(
                    text=f"📢 Предпросмотр рассылки (Получателей: {len(recipients)})\n\n{text_content}",
                    reply_markup=get_broadcast_confirmation_keyboard(),
                    parse_mode=None,
                )
        else:
            raise


@router.callback_query(
    BroadcastStates.waiting_for_confirmation,
    F.data == CB_BROADCAST_CANCEL,
    IsAdmin(),
)
async def cancel_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
    """Cancel mass broadcast."""
    await state.clear()
    if isinstance(callback.message, Message):
        await safe_edit_text(
            message=callback.message,
            text="❌ **Рассылка отменена.**",
            reply_markup=get_admin_main_keyboard(0),
        )
    await callback.answer("Отменено")


@router.callback_query(
    BroadcastStates.waiting_for_confirmation,
    F.data == CB_BROADCAST_CONFIRM,
    IsAdmin(),
)
async def execute_broadcast(
    callback: CallbackQuery,
    state: FSMContext,
    user: User,
    session: AsyncSession,
) -> None:
    """Launch asynchronous mass broadcast task."""
    data = await state.get_data()
    text = data.get("broadcast_text", "")
    photo_file_id = data.get("photo_file_id")
    await state.clear()

    if not callback.bot:
        await callback.answer("Ошибка бота", show_alert=True)
        return

    task = asyncio.create_task(
        broadcast_worker(
            bot=callback.bot,
            session_maker=async_session_maker,
            admin_id=user.id,
            text=text,
            photo_file_id=photo_file_id,
        )
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    if isinstance(callback.message, Message):
        pending_cnt, hist_cnt = await AdminRepository(session).count_reports_by_status()
        await safe_edit_text(
            message=callback.message,
            text=(
                "🚀 **Рассылка запущена в фоновом режиме!**\n\n"
                "Бот отправляет сообщения с плавной скоростью без риска блокировок Telegram. "
                "По окончании вам поступит итоговый отчет."
            ),
            reply_markup=get_admin_main_keyboard(pending_cnt, hist_cnt),
        )
    await callback.answer("Рассылка запущена!")




@router.callback_query(F.data.startswith(CB_ADMIN_HISTORY), IsAdmin())
async def show_reports_history(callback: CallbackQuery, session: AsyncSession) -> None:
    """Display paginated list of archived/resolved complaints."""
    try:
        offset = int(callback.data[len(CB_ADMIN_HISTORY) :])  # type: ignore[index]
    except (IndexError, ValueError):
        offset = 0

    admin_repo = AdminRepository(session)
    reports, total = await admin_repo.get_reports_history(offset=offset, limit=5)

    if not reports and offset == 0:
        text = (
            "📜 **История жалоб пуста**\n\n"
            "Все рассмотренные и закрытые жалобы будут отображаться здесь."
        )
        builder = InlineKeyboardBuilder()
        builder.button(text="⬅️ В панель управления", callback_data=CB_ADMIN_MENU)
        if isinstance(callback.message, Message):
            await safe_edit_text(
                message=callback.message,
                text=text,
                reply_markup=builder.as_markup(),
            )
        await callback.answer()
        return

    reports_slice = [(r.id, r.violator_id, r.reason, r.status) for r in reports]
    keyboard = get_reports_history_keyboard(
        reports_slice=reports_slice,
        offset=offset,
        total_count=total,
        page_size=5,
    )

    text = f"📜 **История рассмотренных жалоб ({total}):**\nВыберите жалобу для просмотра деталей:"
    if isinstance(callback.message, Message):
        await safe_edit_text(
            message=callback.message,
            text=text,
            reply_markup=keyboard,
        )
    await callback.answer()


@router.callback_query(F.data.startswith(CB_ADMIN_HISTORY_VIEW), IsAdmin())
async def view_history_report(callback: CallbackQuery, session: AsyncSession) -> None:
    """Display full details of an archived/resolved complaint."""
    try:
        report_id = int(callback.data[len(CB_ADMIN_HISTORY_VIEW) :])  # type: ignore[index]
    except (IndexError, ValueError):
        await callback.answer("Ошибка параметров", show_alert=True)
        return

    admin_repo = AdminRepository(session)
    report = await admin_repo.get_report_by_id(report_id)
    if report is None:
        await callback.answer("Жалоба не найдена", show_alert=True)
        return

    status_icon = "✅ Решена" if report.status == "resolved" else "❌ Отклонена"
    created_str = report.created_at.strftime("%d.%m.%Y %H:%M")
    reporter_str = (
        f"`{report.reporter_id}` (@{report.reporter.username or '—'})"
        if report.reporter
        else f"`{report.reporter_id}`"
    )
    violator_str = (
        f"`{report.violator_id}` (@{report.violator.username or '—'})"
        if report.violator
        else f"`{report.violator_id}`"
    )

    text = (
        f"📜 **Архивная жалоба #{report.id}** ({status_icon})\n\n"
        f"📅 Дата создания: **{created_str}**\n"
        f"👤 Заявитель: {reporter_str}\n"
        f"🚨 Нарушитель: {violator_str}\n"
        f"📦 Лот ID: `{report.ad_id or '—'}`\n"
        f"💬 Чат ID: `{report.chat_id or '—'}`\n\n"
        f"📝 Причина жалобы: **{report.reason}**"
    )

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="⬅️ К истории", callback_data=f"{CB_ADMIN_HISTORY}0"),
        InlineKeyboardButton(text="⬅️ В панель", callback_data=CB_ADMIN_MENU),
    )

    if isinstance(callback.message, Message):
        await safe_edit_text(
            message=callback.message,
            text=text,
            reply_markup=builder.as_markup(),
        )
    await callback.answer()
