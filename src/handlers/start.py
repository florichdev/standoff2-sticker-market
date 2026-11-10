import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardRemove
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.settings_manager import get_referrals_for_top
from src.database.models import User
from src.database.repositories.ad_repo import AdRepository
from src.database.repositories.referral_repo import ReferralRepository
from src.keyboards.buyer import get_ad_card_keyboard
from src.keyboards.main_menu import BTN_BACK_MENU, BTN_HIDE_MENU, get_main_menu_keyboard
from src.utils.formatters import format_ad_caption
from src.utils.helpers import safe_send_photo

logger = logging.getLogger(__name__)
router = Router(name="start")

WELCOME_TEXT = (
    "✨ <b>Добро пожаловать в Standsticker!</b>\n\n"
    "Единая P2P-витрина для безопасного поиска, покупки и продажи скинов с редкими наклейками.\n\n"
    "🔹 <b>Возможности платформы:</b>\n"
    "▫️ 🛒 <b>Купить скин</b> — витрина актуальных предложений и умный фильтр\n"
    "▫️ 💰 <b>Продать скин</b> — быстрое размещение своего лота со скриншотом\n"
    "▫️ 📦 <b>Мои объявления</b> — управление своими лотами и статистика переходов\n"
    "▫️ 💬 <b>Анонимный чат</b> — безопасная переписка без раскрытия контактов\n"
    "▫️ 🔔 <b>Уведомления</b> — мгновенные оповещения о скинах под ваши параметры\n\n"
    "Выберите раздел в меню ниже 👇"
)


@router.message(CommandStart())
async def handle_start(
    message: Message,
    state: FSMContext,
    user: User,
    session: AsyncSession,
    command: CommandObject | None = None,
    is_new_user: bool = False,
) -> None:
    """Handle /start command, deep-linking to ads and referrals, reset active FSM state and display main menu."""
    await state.clear()

    # Handle referral invite payload (e.g. /start ref_123456)
    if command and command.args and command.args.startswith("ref_"):
        raw_ref_id = command.args[4:]
        if raw_ref_id.isdigit():
            referrer_id = int(raw_ref_id)
            if is_new_user and referrer_id != user.id:
                ref_repo = ReferralRepository(session)
                recorded = await ref_repo.register_referral_atomic(
                    referrer_id=referrer_id, new_user_id=user.id
                )
                if recorded and message.bot:
                    threshold = get_referrals_for_top()
                    current_count = await ref_repo.get_referrals_count(referrer_id)
                    if current_count % threshold == 0:
                        new_boosts = await ref_repo.grant_free_boost(referrer_id, count=1)
                        notice_text = (
                            f"🎉 <b>Вы пригласили {threshold} друзей! Вам доступен 1 бесплатный ТОП (Цель достигнута)!</b>\n\n"
                            f"По вашей ссылке зарегистрировался новый пользователь. "
                            f"Всего вами приглашено: <b>{current_count}</b> друзей.\n\n"
                            f"🎁 <b>Вам начислен 1 бесплатный ТОП на 24 часа в копилку наград!</b>\n"
                            f"Доступно бесплатных ТОПов: <b>{new_boosts}</b> шт.\n\n"
                            "Вы сами выбираете, когда и к какому лоту его применить:\n"
                            "• при публикации нового объявления\n"
                            "• в разделе 📦 <b>Мои объявления</b> на уже существующий скин."
                        )
                    else:
                        remaining = threshold - (current_count % threshold)
                        notice_text = (
                            "👋 <b>По вашей ссылке присоединился новый друг!</b>\n\n"
                            f"Приглашено друзей для следующего ТОПа: <b>{current_count % threshold} / {threshold}</b>\n"
                            f"Осталось пригласить еще: <b>{remaining}</b>."
                        )

                    try:
                        await message.bot.send_message(
                            chat_id=referrer_id,
                            text=notice_text,
                        )
                    except TelegramAPIError as exc:
                        logger.debug("Could not send referral notice to %d: %s", referrer_id, exc)

    # Handle deep-link payload (e.g. /start ad_42 from Telegram channel autopost)
    if command and command.args and command.args.startswith("ad_"):
        raw_ad_id = command.args[3:]
        if raw_ad_id.isdigit():
            ad_id = int(raw_ad_id)
            ad_repo = AdRepository(session)
            ad = await ad_repo.get_by_id(ad_id)
            if ad and ad.is_active:
                await ad_repo.increment_ad_views(ad.id)
                caption = format_ad_caption(ad, mode="buyer")
                if message.bot:
                    await safe_send_photo(
                        bot=message.bot,
                        chat_id=message.chat.id,
                        photo=ad.photo_file_id,
                        caption=caption,
                        reply_markup=get_ad_card_keyboard(ad.id),
                        parse_mode="HTML",
                        ad_id=ad.id,
                    )
                await message.answer(
                    text="👆 <b>Карточка выбранного лота открыта.</b>\nДля дальнейших действий воспользуйтесь кнопками под фото или меню ниже 👇",
                    reply_markup=get_main_menu_keyboard(),
                )
                return
            else:
                await message.answer(
                    text=(
                        f"⚠️ <b>Объявление #{ad_id} уже продано или снято с публикации.</b>\n\n"
                        "Вы можете найти другие актуальные предложения в каталоге 👇"
                    ),
                    reply_markup=get_main_menu_keyboard(),
                )
                return

    await message.answer(
        text=WELCOME_TEXT,
        reply_markup=get_main_menu_keyboard(),
    )


@router.message(F.text == BTN_HIDE_MENU)
async def handle_hide_menu(message: Message, state: FSMContext) -> None:
    """Hide reply keyboard and explain how to restore."""
    await state.clear()
    await message.answer(
        text=(
            "🔽 <b>Панель меню скрыта.</b>\n\n"
            "Чтобы открыть её снова, отправьте команду /menu или /start."
        ),
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(F.text == BTN_BACK_MENU)
@router.message(Command("menu"))
@router.message(Command("cancel"))
@router.message(
    F.text.casefold().in_(
        {
            "отмена",
            "❌ отмена",
            "назад",
            "⬅️ назад",
            "главное меню",
            "/menu",
            "меню",
            "🏠 главное меню",
        }
    )
)
async def handle_cancel(message: Message, state: FSMContext) -> None:
    """Cancel current operation and return to main menu."""
    current_state = await state.get_state()
    if current_state is not None:
        await state.clear()
        await message.answer(
            text="❌ <b>Действие отменено.</b> Вы вернулись в главное меню 👇",
            reply_markup=get_main_menu_keyboard(),
        )
    else:
        await message.answer(
            text="Главное меню 👇",
            reply_markup=get_main_menu_keyboard(),
        )


@router.message(Command("help"))
async def handle_help(message: Message) -> None:
    """Show help disclaimer and safety tips."""
    help_text = (
        "ℹ️ <b>Справка и безопасность | Standsticker</b>\n\n"
        "• Все передачи скинов происходят исключительно внутри игры Standoff 2 через рынок.\n"
        "• Бот выступает защищенной витриной и анонимным мостом для общения.\n"
        "• Никогда не сообщайте коды авторизации, логины или пароли от аккаунтов.\n"
        "• При возникновении спорных ситуаций нажмите «⚠️ Пожаловаться» в карточке или чате."
    )
    await message.answer(
        text=help_text,
        reply_markup=get_main_menu_keyboard(),
    )
