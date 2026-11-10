"""Handlers for user profile, statistics and settings."""

import html
import logging
from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.settings_manager import get_max_active_ads, get_referrals_for_top
from src.database.models import User
from src.database.repositories.ad_repo import AdRepository
from src.database.repositories.referral_repo import ReferralRepository
from src.database.repositories.subscription_repo import SubscriptionRepository
from src.keyboards.main_menu import (
    BTN_PROFILE,
    BTN_SETTINGS,
)
from src.keyboards.seller import get_referral_share_keyboard
from src.utils.helpers import safe_edit_message

logger = logging.getLogger(__name__)
router = Router(name="profile")

CB_PROFILE_OPEN: str = "prf:open"
CB_PROFILE_REFERRAL: str = "prf:referral"
CB_SETTINGS_OPEN: str = "stg:open"
CB_SETTINGS_SUBS: str = "stg:subs"
CB_SETTINGS_CLEAR_SUBS: str = "stg:clr_subs"
CB_SETTINGS_CONFIRM_CLEAR: str = "stg:cnf_clr"
CB_SETTINGS_RULES: str = "stg:rules"


def get_profile_keyboard(threshold: int | None = None) -> InlineKeyboardMarkup:
    """Build profile interactive action keyboard with Bot API 9.4 styles."""
    if threshold is None:
        threshold = get_referrals_for_top()
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="📦 Мои объявления",
            callback_data=CB_SETTINGS_OPEN,
            style="primary",
        ),
        InlineKeyboardButton(
            text="🔔 Мои подписки",
            callback_data=CB_SETTINGS_SUBS,
            style="primary",
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=f"🎁 Получить ТОП бесплатно за {threshold} друзей",
            callback_data=CB_PROFILE_REFERRAL,
            style="success",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="⚙️ Настройки бота",
            callback_data=CB_SETTINGS_OPEN,
        )
    )
    return builder.as_markup()


def get_settings_keyboard(has_subs: bool = False) -> InlineKeyboardMarkup:
    """Build settings keyboard with Bot API 9.4 styles."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🔔 Управление подписками",
            callback_data=CB_SETTINGS_SUBS,
            style="primary",
        )
    )
    if has_subs:
        builder.row(
            InlineKeyboardButton(
                text="🗑 Сбросить все поисковые подписки",
                callback_data=CB_SETTINGS_CLEAR_SUBS,
                style="danger",
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="ℹ️ Правила безопасности сделок",
            callback_data=CB_SETTINGS_RULES,
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="👤 Вернуться в профиль",
            callback_data=CB_PROFILE_OPEN,
        )
    )
    return builder.as_markup()


@router.message(F.text == BTN_PROFILE)
@router.message(Command("profile"))
async def open_profile_message(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    """Render user profile statistics."""
    await state.clear()
    if not message.from_user:
        return
    text, keyboard = await _build_profile_view(message.from_user.id, session)
    await message.answer(text=text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data == CB_PROFILE_OPEN)
async def open_profile_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    """Render user profile from callback navigation."""
    if not callback.from_user:
        return
    text, keyboard = await _build_profile_view(callback.from_user.id, session)
    await safe_edit_message(callback, text=text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == CB_PROFILE_REFERRAL)
async def profile_referral_callback(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    """Show personal referral program info, progress, and share button."""
    if not callback.from_user:
        return
    user = await session.get(User, callback.from_user.id)
    free_boosts = user.free_top_boosts if user else 0

    bot_user = await callback.bot.get_me()
    bot_username = bot_user.username or "Standsticker_bot"
    invite_url = f"https://t.me/{bot_username}?start=ref_{callback.from_user.id}"

    ref_repo = ReferralRepository(session)
    ref_count = await ref_repo.get_referrals_count(callback.from_user.id)
    threshold = get_referrals_for_top()
    remainder = ref_count % threshold
    needed = threshold - remainder

    share_text = (
        "🔥 Покупай и продавай редкие скины с наклейками в Standoff 2 "
        "без комиссии в проверенном маркетплейсе Standsticker!"
    )

    text = (
        "🎁 <b>Бесплатный ТОП за приглашение друзей</b>\n\n"
        f"Приглашайте друзей в Standsticker! За каждых <b>{threshold}</b> приглашенных друзей "
        "вы получаете <b>1 бесплатный ТОП на 24 часа</b>, который можно активировать "
        "на любое свое объявление в любое время.\n\n"
        "📊 <b>Ваша статистика:</b>\n"
        f"• Всего приглашено: <b>{ref_count}</b> чел.\n"
        f"• До следующего бонуса: <b>{needed}</b> чел.\n"
        f"• Доступно бесплатных ТОПов: <b>{free_boosts}</b> шт.\n\n"
        "🔗 <b>Ваша реферальная ссылка:</b>\n"
        f"<code>{html.escape(invite_url)}</code>\n\n"
        "💡 <i>Отправьте ссылку друзьям или нажмите «Поделиться», чтобы отправить ее в чат или канал!</i>"
    )

    reply_markup = get_referral_share_keyboard(
        invite_url=invite_url,
        share_text=share_text,
        back_callback=CB_PROFILE_OPEN,
    )
    await safe_edit_message(callback, text=text, reply_markup=reply_markup)
    await callback.answer()


async def _build_profile_view(
    user_id: int, session: AsyncSession
) -> tuple[str, InlineKeyboardMarkup]:
    """Compile aggregated user profile statistics view."""
    user = await session.get(User, user_id)
    ad_repo = AdRepository(session)
    stats = await ad_repo.get_seller_statistics(user_id)
    sub_repo = SubscriptionRepository(session)
    subs = await sub_repo.get_user_subscriptions(user_id)
    ref_repo = ReferralRepository(session)
    ref_count = await ref_repo.get_referrals_count(user_id)
    threshold = get_referrals_for_top()
    free_boosts = user.free_top_boosts if user else 0

    if user:
        username_str = f"@{html.escape(user.username)}" if user.username else "не указан"
        first_name = html.escape(user.first_name or "Пользователь")
        days_in_bot = (datetime.now(UTC) - user.registration_date).days
        reg_date_str = user.registration_date.strftime("%d.%m.%Y")
        if user.ratings_count > 0:
            rating_str = f"{user.rating:.1f} ⭐ ({user.ratings_count} отзывов)"
        else:
            rating_str = "0.0 ⭐ (0 отзывов)"
        status_str = "🟢 Активен" if not user.is_banned else "🔴 Заблокирован"
    else:
        username_str = "не указан"
        first_name = "Пользователь"
        days_in_bot = 0
        reg_date_str = "сегодня"
        rating_str = "0.0 ⭐ (0 отзывов)"
        status_str = "🟢 Активен"

    text = (
        "👤 <b>Личный кабинет и статистика маркета</b>\n\n"
        f"🆔 <b>ID:</b> <code>{user_id}</code>\n"
        f"🏷 <b>Имя:</b> {first_name}\n"
        f"🔗 <b>Юзернейм:</b> {username_str}\n"
        f"📅 <b>В боте с:</b> {reg_date_str} (дней: {days_in_bot})\n"
        f"🛡 <b>Статус аккаунта:</b> {status_str}\n\n"
        "📊 <b>Торговая активность продавца:</b>\n"
        f"⭐️ <b>Рейтинг доверия:</b> {rating_str}\n"
        f"📦 <b>Активных объявлений:</b> {stats['active_ads']} из {get_max_active_ads()}\n"
        f"📈 <b>Всего выставлено за всё время:</b> {stats['total_ads']} лотов\n"
        f"👁 <b>Суммарно просмотров ваших скинов:</b> {stats['total_views']}\n"
        f"💬 <b>Запросов от покупателей в чат:</b> {stats['total_clicks']}\n\n"
        f"🎁 <b>Доступно бесплатных ТОПов:</b> {free_boosts} шт.\n"
        f"👥 <b>Приглашено друзей:</b> {ref_count} чел.\n\n"
        f"🔔 <b>Активных подписок на поиск скинов:</b> {len(subs)}"
    )
    return text, get_profile_keyboard(threshold)


@router.message(F.text == BTN_SETTINGS)
@router.message(Command("settings"))
async def open_settings_message(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    """Render settings screen."""
    await state.clear()
    if not message.from_user:
        return
    text, keyboard = await _build_settings_view(message.from_user.id, session)
    await message.answer(text=text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data == CB_SETTINGS_OPEN)
async def open_settings_callback(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    """Render settings screen from callback."""
    if not callback.from_user:
        return
    text, keyboard = await _build_settings_view(callback.from_user.id, session)
    await safe_edit_message(callback, text=text, reply_markup=keyboard)
    await callback.answer()


async def _build_settings_view(
    user_id: int, session: AsyncSession
) -> tuple[str, InlineKeyboardMarkup]:
    """Compile settings view."""
    sub_repo = SubscriptionRepository(session)
    subs = await sub_repo.get_user_subscriptions(user_id)
    text = (
        "⚙️ <b>Настройки бота и уведомлений</b>\n\n"
        "Здесь вы можете управлять вашими поисковыми фильтрами, подписками и параметрами взаимодействия.\n\n"
        f"🔔 <b>Поисковые подписки:</b> сохранено {len(subs)} шт. "
        "(при появлении подходящего скина бот мгновенно отправляет push-уведомление).\n\n"
        "💬 <b>Чат-сессии:</b> безопасные анонимные диалоги без передачи личных контактов до договоренности."
    )
    return text, get_settings_keyboard(has_subs=len(subs) > 0)


@router.callback_query(F.data == CB_SETTINGS_SUBS)
async def view_subscriptions_callback(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    """List all active search subscriptions for the user."""
    if not callback.from_user:
        return
    sub_repo = SubscriptionRepository(session)
    subs = await sub_repo.get_user_subscriptions(callback.from_user.id)

    if not subs:
        text = (
            "🔔 <b>Ваши поисковые подписки</b>\n\n"
            "У вас пока нет активных сохраненных поисков.\n\n"
            "Чтобы подписаться на появление скина, зайдите в 🛒 <b>Купить скин</b>, "
            "настройте нужные фильтры (оружие, скин, наклейки) и нажмите кнопку <b>🔔 Подписаться на поиск</b>."
        )
        builder = InlineKeyboardBuilder()
        builder.button(text="⬅️ Назад в настройки", callback_data=CB_SETTINGS_OPEN)
        await safe_edit_message(callback, text=text, reply_markup=builder.as_markup())
        await callback.answer()
        return

    text = f"🔔 <b>Ваши активные подписки на скины ({len(subs)}):</b>\n\n"
    for idx, sub in enumerate(subs, 1):
        wpn = html.escape(sub.weapon_model or "Любое оружие")
        skn = html.escape(sub.skin_name or "Любой скин")
        cnt = f"{sub.sticker_count} шт." if sub.sticker_count is not None else "любое"
        rar = html.escape(sub.sticker_rarity or "любая")
        text += (
            f"<b>{idx}.</b> {wpn} | {skn}\n"
            f"   🏷 Наклейки: {cnt} (редкость: {rar})\n"
            f"   📅 Создана: {sub.created_at.strftime('%d.%m.%Y')}\n\n"
        )

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🗑 Очистить все подписки",
            callback_data=CB_SETTINGS_CLEAR_SUBS,
            style="danger",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="⬅️ Назад в настройки",
            callback_data=CB_SETTINGS_OPEN,
        )
    )
    await safe_edit_message(callback, text=text, reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data == CB_SETTINGS_CLEAR_SUBS)
async def confirm_clear_subscriptions(callback: CallbackQuery) -> None:
    """Confirm deletion of all user subscriptions."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="❌ Да, удалить все подписки",
            callback_data=CB_SETTINGS_CONFIRM_CLEAR,
            style="danger",
        ),
        InlineKeyboardButton(
            text="⬅️ Отмена",
            callback_data=CB_SETTINGS_OPEN,
        ),
    )
    await safe_edit_message(
        callback,
        text="⚠️ <b>Вы уверены, что хотите удалить все свои поисковые подписки?</b>\n"
        "Вы перестанете получать push-уведомления о новых скинах в ленте.",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == CB_SETTINGS_CONFIRM_CLEAR)
async def execute_clear_subscriptions(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    """Execute deletion of all user subscriptions."""
    if not callback.from_user:
        return
    sub_repo = SubscriptionRepository(session)
    count = await sub_repo.clear_user_subscriptions(callback.from_user.id)
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ В настройки", callback_data=CB_SETTINGS_OPEN)
    await safe_edit_message(
        callback,
        text=f"✅ Успешно удалено {count} поисковых подписок.",
        reply_markup=builder.as_markup(),
    )
    await callback.answer("Подписки удалены")


@router.callback_query(F.data == CB_SETTINGS_RULES)
async def show_rules_callback(callback: CallbackQuery) -> None:
    """Display trading safety rules and guidelines."""
    text = (
        "ℹ️ <b>Правила безопасности сделок на Standsticker</b>\n\n"
        "1. <b>Никогда не передавайте скины или голду вперед</b>, если не уверены в партнере.\n"
        "2. Все переговоры ведите строго <b>внутри анонимного чата бота</b> — все сообщения сохраняются для разрешения споров арбитражем.\n"
        "3. Если покупатель или продавец ведет себя подозрительно, требует перейти в сторонний мессенджер или угрожает — нажмите кнопку <b>⚠️ Пожаловаться</b>.\n"
        "4. Проверяйте репутацию и отзывы продавца перед заключением сделки.\n\n"
        "Администрация бота круглосуточно модерирует жалобы и блокирует мошенников навсегда."
    )
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Назад в настройки", callback_data=CB_SETTINGS_OPEN)
    await safe_edit_message(callback, text=text, reply_markup=builder.as_markup())
    await callback.answer()
