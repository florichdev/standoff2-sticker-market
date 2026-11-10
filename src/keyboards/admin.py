from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.config import settings

CB_REPORT_REASON: str = "rep_rsn:"
CB_REPORT_CANCEL: str = "rep_cancel"

CB_ADMIN_MENU: str = "adm_menu"
CB_ADMIN_REPORTS: str = "adm_reps:"
CB_ADMIN_REPORT_VIEW: str = "adm_rep_v:"
CB_ADMIN_HISTORY: str = "adm_hist:"
CB_ADMIN_HISTORY_VIEW: str = "adm_hv:"
CB_ADMIN_BAN: str = "adm_ban:"
CB_ADMIN_UNBAN: str = "adm_unban:"
CB_ADMIN_DISMISS: str = "adm_dsm:"
CB_ADMIN_RESOLVE: str = "adm_res:"
CB_ADMIN_VIEW_AD: str = "adm_vad:"
CB_ADMIN_DEL_AD: str = "adm_dad:"
CB_ADMIN_LOG: str = "adm_log:"
CB_ADMIN_SEARCH: str = "adm_search"
CB_ADMIN_BROADCAST: str = "adm_bc"
CB_BROADCAST_CONFIRM: str = "adm_bc_confirm"
CB_BROADCAST_CANCEL: str = "adm_bc_cancel"


def get_report_reasons_keyboard() -> InlineKeyboardMarkup:
    """Build selection keyboard for report reason."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="⚠️ Скам / Обман", callback_data=f"{CB_REPORT_REASON}scam"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🚫 Запрещенный контент",
            callback_data=f"{CB_REPORT_REASON}illegal",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🤬 Оскорбления / Спам",
            callback_data=f"{CB_REPORT_REASON}abuse",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="❓ Другая причина", callback_data=f"{CB_REPORT_REASON}other"
        )
    )
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data=CB_REPORT_CANCEL))
    return builder.as_markup()


def get_admin_main_keyboard(
    pending_reports_count: int,
    history_reports_count: int = 0,
) -> InlineKeyboardMarkup:
    """Build main control dashboard for administrators."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=f"📋 Жалобы ({pending_reports_count})",
            callback_data=f"{CB_ADMIN_REPORTS}0",
        ),
        InlineKeyboardButton(
            text=f"📜 История ({history_reports_count})",
            callback_data=f"{CB_ADMIN_HISTORY}0",
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="🔍 Найти пользователя", callback_data=CB_ADMIN_SEARCH
        ),
        InlineKeyboardButton(
            text="📢 Массовая рассылка", callback_data=CB_ADMIN_BROADCAST
        ),
    )
    builder.row(
        InlineKeyboardButton(text="🔄 Обновить сводку", callback_data=CB_ADMIN_MENU)
    )
    return builder.as_markup()


def get_reports_list_keyboard(
    reports_slice: list[tuple[int, int, str]],
    offset: int,
    total_count: int,
    page_size: int = 5,
) -> InlineKeyboardMarkup:
    """Build paginated list of pending reports."""
    builder = InlineKeyboardBuilder()
    for rep_id, violator_id, reason in reports_slice:
        short_reason = reason[:20] + "..." if len(reason) > 20 else reason
        builder.row(
            InlineKeyboardButton(
                text=f"#{rep_id} | Юзер {violator_id} ({short_reason})",
                callback_data=f"{CB_ADMIN_REPORT_VIEW}{rep_id}",
            )
        )

    nav_row: list[InlineKeyboardButton] = []
    if offset > 0:
        nav_row.append(
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=f"{CB_ADMIN_REPORTS}{offset - page_size}",
            )
        )
    if offset + page_size < total_count:
        nav_row.append(
            InlineKeyboardButton(
                text="Вперед ➡️",
                callback_data=f"{CB_ADMIN_REPORTS}{offset + page_size}",
            )
        )
    if nav_row:
        builder.row(*nav_row)

    builder.row(
        InlineKeyboardButton(text="⬅️ В панель управления", callback_data=CB_ADMIN_MENU)
    )
    return builder.as_markup()


def get_reports_history_keyboard(
    reports_slice: list[tuple[int, int, str, str]],
    offset: int,
    total_count: int,
    page_size: int = 5,
) -> InlineKeyboardMarkup:
    """Build paginated list of archived/resolved complaints."""
    builder = InlineKeyboardBuilder()
    for rep_id, violator_id, reason, status in reports_slice:
        short_reason = reason[:16] + "..." if len(reason) > 16 else reason
        icon = "✅" if status == "resolved" else "❌"
        builder.row(
            InlineKeyboardButton(
                text=f"{icon} #{rep_id} | ID {violator_id} ({short_reason})",
                callback_data=f"{CB_ADMIN_HISTORY_VIEW}{rep_id}",
            )
        )

    nav_row: list[InlineKeyboardButton] = []
    if offset > 0:
        nav_row.append(
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=f"{CB_ADMIN_HISTORY}{offset - page_size}",
            )
        )
    if offset + page_size < total_count:
        nav_row.append(
            InlineKeyboardButton(
                text="Вперед ➡️",
                callback_data=f"{CB_ADMIN_HISTORY}{offset + page_size}",
            )
        )
    if nav_row:
        builder.row(*nav_row)

    builder.row(
        InlineKeyboardButton(text="⬅️ В панель управления", callback_data=CB_ADMIN_MENU)
    )
    return builder.as_markup()


def get_report_action_keyboard(
    report_id: int,
    violator_id: int,
    chat_id: int | None = None,
    ad_id: int | None = None,
) -> InlineKeyboardMarkup:
    """Build moderation action buttons for a single report."""
    builder = InlineKeyboardBuilder()

    if ad_id:
        builder.row(
            InlineKeyboardButton(
                text="📦 Посмотреть лот",
                callback_data=f"{CB_ADMIN_VIEW_AD}{ad_id}:{report_id}",
            ),
            InlineKeyboardButton(
                text="🗑 Снять лот с продажи",
                callback_data=f"{CB_ADMIN_DEL_AD}{ad_id}:{report_id}",
            ),
        )

    if chat_id:
        builder.row(
            InlineKeyboardButton(
                text="📜 Выгрузить лог чата",
                callback_data=f"{CB_ADMIN_LOG}{chat_id}:{report_id}",
            )
        )

    if violator_id not in settings.ADMIN_IDS:
        builder.row(
            InlineKeyboardButton(
                text="🔨 Забанить продавца (1-клик)",
                callback_data=f"{CB_ADMIN_BAN}{violator_id}:{report_id}",
            )
        )

    builder.row(
        InlineKeyboardButton(
            text="✅ Принять жалобу",
            callback_data=f"{CB_ADMIN_RESOLVE}{report_id}",
        ),
        InlineKeyboardButton(
            text="❌ Отклонить",
            callback_data=f"{CB_ADMIN_DISMISS}{report_id}",
        ),
    )
    builder.row(
        InlineKeyboardButton(text="⬅️ К списку", callback_data=f"{CB_ADMIN_REPORTS}0"),
    )
    return builder.as_markup()


def get_user_action_keyboard(
    target_user_id: int, is_banned: bool
) -> InlineKeyboardMarkup:
    """Build action buttons for a specific user card in admin search."""
    builder = InlineKeyboardBuilder()
    if target_user_id in settings.ADMIN_IDS:
        builder.row(
            InlineKeyboardButton(
                text="👑 Администратор (бан недоступен)",
                callback_data="noop_admin_shield",
            )
        )
    elif not is_banned:
        builder.row(
            InlineKeyboardButton(
                text="🔨 Забанить пользователя",
                callback_data=f"{CB_ADMIN_BAN}{target_user_id}:0",
            )
        )
    else:
        builder.row(
            InlineKeyboardButton(
                text="🤝 Разбанить пользователя",
                callback_data=f"{CB_ADMIN_UNBAN}{target_user_id}",
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="🔍 Найти другого",
            callback_data=CB_ADMIN_SEARCH,
        ),
        InlineKeyboardButton(text="⬅️ В панель", callback_data=CB_ADMIN_MENU),
    )
    return builder.as_markup()


def get_broadcast_confirmation_keyboard() -> InlineKeyboardMarkup:
    """Build confirmation keyboard before launching mass broadcast."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🚀 Запустить рассылку",
            callback_data=CB_BROADCAST_CONFIRM,
        ),
        InlineKeyboardButton(
            text="❌ Отмена",
            callback_data=CB_BROADCAST_CANCEL,
        ),
    )
    return builder.as_markup()
