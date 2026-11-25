import html
import logging
import re
from typing import Any

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import User
from src.database.repositories.ad_repo import AdRepository
from src.database.repositories.chat_repo import ChatRepository
from src.keyboards.admin import get_report_reasons_keyboard
from src.keyboards.buyer import CB_START_CHAT
from src.keyboards.chat import (
    CB_CHAT_CLOSE,
    CB_CHAT_PAUSE,
    CB_CHAT_REPORT,
    CB_CHAT_RESUME,
    CB_DEAL_CONFIRM,
    CB_DEAL_REJECT,
    CB_RATE_SELLER,
    get_chat_control_keyboard,
    get_chat_incoming_keyboard,
    get_deal_confirmation_keyboard,
    get_my_chats_keyboard,
    get_rating_keyboard,
)
from src.keyboards.main_menu import (
    BTN_BACK_MENU,
    BTN_BUY_SKIN,
    BTN_CHATS,
    BTN_FEED_FILTER,
    BTN_FEED_MORE,
    BTN_HIDE_MENU,
    BTN_MY_ADS,
    BTN_PROFILE,
    BTN_SELL_SKIN,
    BTN_SETTINGS,
    get_main_menu_keyboard,
)
from src.states.admin import ReportStates
from src.states.chat import ChatStates

logger = logging.getLogger(__name__)
router = Router(name="chat")

MENU_BUTTONS: set[str] = {
    BTN_BUY_SKIN,
    BTN_SELL_SKIN,
    BTN_MY_ADS,
    BTN_CHATS,
    BTN_PROFILE,
    BTN_SETTINGS,
    BTN_HIDE_MENU,
    BTN_FEED_FILTER,
    BTN_FEED_MORE,
    BTN_BACK_MENU,
}




@router.callback_query(F.data.startswith(CB_START_CHAT))
async def handle_start_chat(
    callback: CallbackQuery,
    user: User,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Initiate an anonymous proxy chat session between buyer and seller."""
    try:
        ad_id = int(callback.data[len(CB_START_CHAT) :])  # type: ignore[index]
    except (IndexError, ValueError):
        await callback.answer("Ошибка параметров лота.", show_alert=True)
        return

    ad_repo = AdRepository(session)
    chat_repo = ChatRepository(session)

    ad = await ad_repo.get_by_id(ad_id)
    if ad is None or not ad.is_active:
        await callback.answer("Объявление больше недоступно.", show_alert=True)
        return

    if ad.seller_id == user.id:
        await callback.answer(
            "⚠️ Вы не можете написать самому себе (это ваше объявление)!",
            show_alert=True,
        )
        return

    chat_session, error = await chat_repo.create_chat_session_guarded(
        ad_id=ad.id,
        buyer_id=user.id,
        seller_id=ad.seller_id,
    )

    if chat_session is None:
        await callback.answer(
            "⚠️ Не удалось начать диалог. Пожалуйста, попробуйте позже.",
            show_alert=True,
        )
        return

    await ad_repo.increment_ad_clicks(ad.id)

    await state.set_state(ChatStates.in_chat)
    await state.update_data(active_session_id=chat_session.id)

    sticker_label = (
        "Без наклеек"
        if ad.sticker_count == 0
        else f"{ad.sticker_count} шт."
    )
    ad_title = html.escape(f"{ad.weapon_model} | {ad.skin_name}")

    buyer_text = (
        "💬 <b>Анонимный чат с продавцом открыт!</b>\n\n"
        f"📦 <b>Лот:</b> {ad_title}\n"
        f"💰 <b>Цена:</b> {ad.price_gold} G\n"
        f"🏷 <b>Наклеек:</b> {sticker_label}\n\n"
        "🛡 <b>Правила безопасности:</b>\n"
        "• Чат полностью анонимен: продавец не видит ваш профиль Telegram.\n"
        "• Не переходите по внешним ссылкам и не сообщайте коды авторизации.\n"
        "• Передача скина осуществляется внутри игры Standoff 2.\n\n"
        "Отправьте сообщение продавцу прямо в этот диалог 👇"
    )

    buyer_keyboard = get_chat_control_keyboard(chat_session.id)

    if callback.bot:
        await callback.bot.send_message(
            chat_id=user.id,
            text=buyer_text,
            reply_markup=buyer_keyboard,
        )

        seller_key = StorageKey(
            bot_id=callback.bot.id,
            chat_id=ad.seller_id,
            user_id=ad.seller_id,
        )
        seller_state_ctx = FSMContext(storage=state.storage, key=seller_key)
        seller_cur_state = await seller_state_ctx.get_state()
        seller_data = await seller_state_ctx.get_data()
        seller_is_focused = (
            seller_cur_state == ChatStates.in_chat.state
            and seller_data.get("active_session_id") == chat_session.id
        )

        seller_text = (
            "💬 <b>Покупатель интересуется вашим лотом!</b>\n\n"
            f"📦 <b>Лот:</b> {ad_title}\n"
            f"💰 <b>Цена:</b> {ad.price_gold} G\n"
            f"🏷 <b>Наклеек:</b> {sticker_label}\n\n"
            "🛡 <b>Правила безопасности:</b>\n"
            "• Чат полностью анонимен: покупатель не видит ваш профиль Telegram.\n"
            "• Не передавайте личные реквизиты и контакты вне маркета.\n\n"
            "Нажмите «💬 Ответить в чате» или напишите сообщение прямо сюда 👇"
        )
        seller_kb = (
            get_chat_control_keyboard(chat_session.id)
            if seller_is_focused
            else get_chat_incoming_keyboard(chat_session.id)
        )

        try:
            await callback.bot.send_message(
                chat_id=ad.seller_id,
                text=seller_text,
                reply_markup=seller_kb,
            )
        except (TelegramForbiddenError, TelegramBadRequest) as e:
            logger.warning(
                "Could not deliver chat notification to seller %d: %s",
                ad.seller_id,
                e,
            )

    await callback.answer("Чат открыт!")




@router.callback_query(F.data.startswith(CB_CHAT_PAUSE))
async def handle_chat_pause(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Pause/minimize current chat session back to main menu without terminating it."""
    await state.clear()
    pause_text = (
        "⏸ <b>Диалог свернут.</b>\n\n"
        "Вы можете вернуться к нему в любой момент через раздел <b>«💬 Мои диалоги»</b> "
        "или по кнопке в уведомлении, когда собеседник пришлет ответ."
    )
    if isinstance(callback.message, Message):
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        await callback.message.answer(
            text=pause_text,
            reply_markup=get_main_menu_keyboard(),
        )
    await callback.answer("Диалог свернут")


@router.callback_query(F.data.startswith(CB_CHAT_RESUME))
async def handle_chat_resume(
    callback: CallbackQuery,
    user: User,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Resume an active chat session from background and focus user on it."""
    try:
        session_id = int(callback.data[len(CB_CHAT_RESUME) :])  # type: ignore[index]
    except (IndexError, ValueError):
        await callback.answer("Ошибка параметров диалога.", show_alert=True)
        return

    chat_repo = ChatRepository(session)
    active_chat = await chat_repo.get_active_session_by_id(session_id)
    if active_chat is None or not active_chat.is_active:
        await callback.answer("Этот диалог уже завершен.", show_alert=True)
        return

    if user.id not in (active_chat.buyer_id, active_chat.seller_id):
        await callback.answer("У вас нет доступа к этому диалогу.", show_alert=True)
        return

    await state.set_state(ChatStates.in_chat)
    await state.update_data(active_session_id=active_chat.id)

    ad = active_chat.ad
    ad_title = html.escape(
        f"{ad.weapon_model} | {ad.skin_name}" if ad else f"Лот #{active_chat.ad_id}"
    )
    is_buyer = user.id == active_chat.buyer_id
    partner_role = "продавцом" if is_buyer else "покупателем"

    last_messages = await chat_repo.get_chat_last_messages(active_chat.id, limit=5)

    body = (
        f"💬 <b>Диалог по лоту {ad_title} открыт!</b>\n"
        f"Вы общаетесь с {partner_role}.\n\n"
    )
    if last_messages:
        body += "📜 <b>Последние сообщения:</b>\n"
        for msg in last_messages:
            sender = (
                "Покупатель" if msg.sender_id == active_chat.buyer_id else "Продавец"
            )
            content = html.escape(msg.text_content) if msg.text_content else "[Изображение]"
            body += f"• <b>{sender}</b>: {content}\n"
        body += "\n"

    body += "Отправьте сообщение собеседнику прямо в этот диалог 👇"

    keyboard = get_chat_control_keyboard(active_chat.id)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            text=body,
            reply_markup=keyboard,
        )
    await callback.answer("Диалог открыт")


@router.message(F.text == BTN_CHATS)
@router.message(Command("chats"))
async def handle_my_chats(
    message: Message,
    user: User,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Display list of all open dialogs for user."""
    await state.clear()
    chat_repo = ChatRepository(session)
    chats = await chat_repo.get_user_active_sessions(user.id)

    if not chats:
        await message.answer(
            text=(
                "💬 <b>Мои активные диалоги</b>\n\n"
                "У вас пока нет открытых диалогов с продавцами или покупателями.\n\n"
                "Чтобы начать диалог, выберите скин в витрине (кнопка «🛒 Купить скин») "
                "и нажмите «💬 Написать продавцу»."
            ),
            reply_markup=get_main_menu_keyboard(),
        )
        return

    text = (
        f"💬 <b>Ваши активные диалоги ({len(chats)}):</b>\n\n"
        "Выберите диалог, чтобы открыть переписку и ответить собеседнику 👇"
    )
    await message.answer(
        text=text,
        reply_markup=get_my_chats_keyboard(chats, user.id),
    )


@router.message(F.text.regexp(r"^/chat_(\d+)$").as_("match"))
async def handle_chat_shortcut(
    message: Message,
    match: re.Match[str],
    user: User,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Handle direct /chat_<id> command shortcut."""
    session_id = int(match.group(1))
    chat_repo = ChatRepository(session)
    active_chat = await chat_repo.get_active_session_by_id(session_id)
    if active_chat is None or not active_chat.is_active:
        await message.answer(
            "⚠️ Этот диалог уже завершен.",
            reply_markup=get_main_menu_keyboard(),
        )
        return

    if user.id not in (active_chat.buyer_id, active_chat.seller_id):
        await message.answer(
            "⚠️ У вас нет доступа к этому диалогу.",
            reply_markup=get_main_menu_keyboard(),
        )
        return

    await state.set_state(ChatStates.in_chat)
    await state.update_data(active_session_id=active_chat.id)

    ad = active_chat.ad
    raw_title = (
        f"{ad.weapon_model} | {ad.skin_name}" if ad else f"Лот #{active_chat.ad_id}"
    )
    ad_title = html.escape(raw_title)
    is_buyer = user.id == active_chat.buyer_id
    partner_role = "продавцом" if is_buyer else "покупателем"

    last_messages = await chat_repo.get_chat_last_messages(active_chat.id, limit=5)
    body = (
        f"💬 <b>Диалог по лоту {ad_title} открыт!</b>\n"
        f"Вы общаетесь с {partner_role}.\n\n"
    )
    if last_messages:
        body += "📜 <b>Последние сообщения:</b>\n"
        for msg in last_messages:
            sender = (
                "Покупатель" if msg.sender_id == active_chat.buyer_id else "Продавец"
            )
            content = html.escape(msg.text_content) if msg.text_content else "[Изображение]"
            body += f"• <b>{sender}</b>: {content}\n"
        body += "\n"

    body += "Отправьте сообщение собеседнику прямо в диалог 👇"
    await message.answer(
        text=body,
        reply_markup=get_chat_control_keyboard(active_chat.id),
    )




@router.message(
    ChatStates.in_chat,
    F.text,
    ~F.text.startswith("/"),
    ~F.text.in_(MENU_BUTTONS),
)
async def relay_chat_text(
    message: Message,
    user: User,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Relay incoming text message to the other participant in active chat."""
    if not message.text:
        return

    data = await state.get_data()
    session_id = data.get("active_session_id")
    if session_id is None:
        await state.clear()
        return

    chat_repo = ChatRepository(session)
    active_chat = await chat_repo.get_active_session_by_id(session_id)
    if active_chat is None or not active_chat.is_active:
        await state.clear()
        await message.answer(
            text="⚠️ Этот диалог уже завершен.",
            reply_markup=get_main_menu_keyboard(),
        )
        return

    if user.id == active_chat.buyer_id:
        recipient_id = active_chat.seller_id
        sender_label = "Покупатель"
    else:
        recipient_id = active_chat.buyer_id
        sender_label = "Продавец"

    await chat_repo.save_chat_message(
        session_id=active_chat.id,
        sender_id=user.id,
        content_type="text",
        text_content=message.text,
    )

    if not message.bot:
        return

    recipient_key = StorageKey(
        bot_id=message.bot.id, chat_id=recipient_id, user_id=recipient_id
    )
    recipient_fsm = FSMContext(storage=state.storage, key=recipient_key)
    rec_state = await recipient_fsm.get_state()
    rec_data = await recipient_fsm.get_data()
    is_focused = (
        rec_state == ChatStates.in_chat.state
        and rec_data.get("active_session_id") == active_chat.id
    )

    ad = active_chat.ad
    lot_title = (
        f"{ad.weapon_model} | {ad.skin_name}" if ad else f"Лоту #{active_chat.ad_id}"
    )

    escaped_msg = html.escape(message.text)
    if is_focused:
        text_content = f"👤 <b>[{sender_label}]</b>:\n{escaped_msg}"
        reply_markup = get_chat_control_keyboard(active_chat.id)
    else:
        escaped_lot = html.escape(lot_title)
        text_content = (
            f"📩 <b>Новое сообщение по {escaped_lot}</b>\n\n"
            f"👤 <b>{sender_label}:</b>\n{escaped_msg}\n\n"
            "Нажмите «💬 Ответить в чате», чтобы продолжить диалог 👇"
        )
        reply_markup = get_chat_incoming_keyboard(active_chat.id)

    try:
        await message.bot.send_message(
            chat_id=recipient_id,
            text=text_content,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML,
        )
    except TelegramBadRequest as exc:
        err_str = str(exc).lower()
        if "can't parse entities" in err_str:
            logger.warning(
                "Entity parsing failed for chat message to %d; falling back to unformatted text: %s",
                recipient_id,
                exc,
            )
            raw_text = f"👤 [{sender_label}]:\n{message.text}"
            await message.bot.send_message(
                chat_id=recipient_id,
                text=raw_text,
                reply_markup=reply_markup,
                parse_mode=None,
            )
        elif "chat not found" in err_str or "user is deactivated" in err_str:
            logger.info(
                "Recipient %d unavailable (%s); closing chat session %d",
                recipient_id,
                exc,
                active_chat.id,
            )
            await message.answer(
                "⚠️ Собеседник недоступен (аккаунт удален или чат не найден). Чат закрыт.",
                reply_markup=get_main_menu_keyboard(),
            )
            await state.clear()
            await chat_repo.close_chat_session(active_chat.id)
        else:
            raise
    except TelegramForbiddenError:
        await message.answer(
            "⚠️ Собеседник заблокировал бота. Чат будет закрыт.",
            reply_markup=get_main_menu_keyboard(),
        )
        await state.clear()
        await chat_repo.close_chat_session(active_chat.id)


@router.message(ChatStates.in_chat, F.photo)
async def relay_chat_photo(
    message: Message,
    user: User,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Relay photo messages between participants without revealing user identity."""
    if not message.photo:
        return

    data = await state.get_data()
    session_id = data.get("active_session_id")
    if session_id is None:
        await state.clear()
        return

    chat_repo = ChatRepository(session)
    active_chat = await chat_repo.get_active_session_by_id(session_id)
    if active_chat is None or not active_chat.is_active:
        await state.clear()
        await message.answer(
            text="⚠️ Этот диалог уже завершен.",
            reply_markup=get_main_menu_keyboard(),
        )
        return

    if user.id == active_chat.buyer_id:
        recipient_id = active_chat.seller_id
        sender_label = "Покупатель"
    else:
        recipient_id = active_chat.buyer_id
        sender_label = "Продавец"

    photo_file_id = message.photo[-1].file_id

    await chat_repo.save_chat_message(
        session_id=active_chat.id,
        sender_id=user.id,
        content_type="photo",
        text_content=message.caption,
        file_id=photo_file_id,
    )

    if not message.bot:
        return

    recipient_key = StorageKey(
        bot_id=message.bot.id, chat_id=recipient_id, user_id=recipient_id
    )
    recipient_fsm = FSMContext(storage=state.storage, key=recipient_key)
    rec_state = await recipient_fsm.get_state()
    rec_data = await recipient_fsm.get_data()
    is_focused = (
        rec_state == ChatStates.in_chat.state
        and rec_data.get("active_session_id") == active_chat.id
    )

    ad = active_chat.ad
    lot_title = (
        f"{ad.weapon_model} | {ad.skin_name}" if ad else f"Лоту #{active_chat.ad_id}"
    )

    escaped_caption = f"\n{html.escape(message.caption)}" if message.caption else ""
    if is_focused:
        caption_html = f"👤 <b>[{sender_label}]</b>{escaped_caption}"
        reply_markup = get_chat_control_keyboard(active_chat.id)
    else:
        escaped_lot = html.escape(lot_title)
        caption_html = (
            f"📩 <b>Новое фото по {escaped_lot}</b>\n\n"
            f"👤 <b>{sender_label}</b> прислал скриншот.{escaped_caption}\n\n"
            "Нажмите «💬 Ответить в чате», чтобы продолжить диалог 👇"
        )
        reply_markup = get_chat_incoming_keyboard(active_chat.id)

    try:
        if len(caption_html) <= 1024:
            await message.bot.send_photo(
                chat_id=recipient_id,
                photo=photo_file_id,
                caption=caption_html,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML,
            )
        else:
            await message.bot.send_photo(
                chat_id=recipient_id,
                photo=photo_file_id,
            )
            await message.bot.send_message(
                chat_id=recipient_id,
                text=caption_html,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML,
            )
    except TelegramBadRequest as exc:
        err_str = str(exc).lower()
        if "can't parse entities" in err_str:
            logger.warning(
                "Entity parsing failed for chat photo to %d; falling back to unformatted caption: %s",
                recipient_id,
                exc,
            )
            raw_caption = f"👤 [{sender_label}]\n{message.caption}" if message.caption else f"👤 [{sender_label}]"
            await message.bot.send_photo(
                chat_id=recipient_id,
                photo=photo_file_id,
                caption=raw_caption[:1024],
                reply_markup=reply_markup,
                parse_mode=None,
            )
        elif "chat not found" in err_str or "user is deactivated" in err_str:
            logger.info(
                "Recipient %d unavailable (%s); closing chat session %d",
                recipient_id,
                exc,
                active_chat.id,
            )
            await message.answer(
                "⚠️ Собеседник недоступен (аккаунт удален или чат не найден). Чат закрыт.",
                reply_markup=get_main_menu_keyboard(),
            )
            await state.clear()
            await chat_repo.close_chat_session(active_chat.id)
        else:
            raise
    except TelegramForbiddenError:
        await message.answer(
            "⚠️ Собеседник заблокировал бота. Чат будет закрыт.",
            reply_markup=get_main_menu_keyboard(),
        )
        await state.clear()
        await chat_repo.close_chat_session(active_chat.id)


@router.message(ChatStates.in_chat, ~F.text & ~F.photo & ~F.successful_payment)
async def reject_unsupported_media_in_chat(message: Message) -> None:
    """Inform user that only text and photos are supported in proxy chat."""
    await message.answer(
        "⚠️ В анонимном чате поддерживаются только текстовые сообщения и скриншоты (фото).",
    )


@router.callback_query(F.data.startswith(CB_CHAT_REPORT))
async def handle_chat_report(
    callback: CallbackQuery,
    user: User,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Prompt user to report their chat partner from within the conversation."""
    try:
        session_id = int(callback.data[len(CB_CHAT_REPORT) :])  # type: ignore[index]
    except (IndexError, ValueError):
        await callback.answer("Ошибка параметров сессии", show_alert=True)
        return

    chat_repo = ChatRepository(session)
    active_chat = await chat_repo.get_active_session_by_id(session_id)
    if active_chat is None or user.id not in (
        active_chat.buyer_id,
        active_chat.seller_id,
    ):
        await callback.answer("Чат уже завершен или недоступен.", show_alert=True)
        return

    partner_id = (
        active_chat.seller_id
        if user.id == active_chat.buyer_id
        else active_chat.buyer_id
    )

    await state.set_state(ReportStates.waiting_for_reason)
    await state.update_data(
        ad_id=active_chat.ad_id,
        violator_id=partner_id,
        chat_id=active_chat.id,
    )

    if isinstance(callback.message, Message):
        await callback.message.reply(
            text=(
                f"⚠️ <b>Пожаловаться на собеседника в чате #{active_chat.id}</b>\n\n"
                "Пожалуйста, выберите причину нарушения правил платформы:"
            ),
            reply_markup=get_report_reasons_keyboard(),
        )
    await callback.answer()




@router.callback_query(F.data.startswith(CB_CHAT_CLOSE))
async def handle_chat_close(
    callback: CallbackQuery,
    user: User,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Close active chat session, notify both parties, and prompt buyer for rating."""
    try:
        session_id = int(callback.data[len(CB_CHAT_CLOSE) :])  # type: ignore[index]
    except (IndexError, ValueError):
        await callback.answer("Ошибка параметров сессии", show_alert=True)
        return

    chat_repo = ChatRepository(session)
    active_chat = await chat_repo.get_active_session_by_id(session_id)

    if active_chat is None or user.id not in (
        active_chat.buyer_id,
        active_chat.seller_id,
    ):
        await callback.answer("Чат уже завершен или недоступен.", show_alert=True)
        return

    await chat_repo.close_chat_session(session_id)

    cur_data = await state.get_data()
    if cur_data.get("active_session_id") == session_id:
        await state.clear()

    if isinstance(callback.message, Message):
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass

    other_id = (
        active_chat.seller_id
        if user.id == active_chat.buyer_id
        else active_chat.buyer_id
    )

    if callback.bot:
        other_key = StorageKey(
            bot_id=callback.bot.id, chat_id=other_id, user_id=other_id
        )
        other_fsm = FSMContext(storage=state.storage, key=other_key)
        other_data = await other_fsm.get_data()
        if other_data.get("active_session_id") == session_id:
            await other_fsm.clear()

    closed_notice = (
        "❌ <b>Диалог завершен.</b>\n"
        "Чат закрыт одним из участников. Сообщения больше не пересылаются."
    )

    deal_prompt = (
        "🤝 <b>Состоялась ли сделка между вами?</b>\n\n"
        "Подтвердите, пожалуйста, была ли совершена передача скина / оплата.\n"
        "<i>Оценка продавца станет доступна только после подтверждения обеими сторонами.</i>"
    )
    deal_kb = get_deal_confirmation_keyboard(session_id=active_chat.id)

    if callback.bot:
        await callback.bot.send_message(
            chat_id=user.id,
            text=closed_notice,
            reply_markup=get_main_menu_keyboard(),
        )
        try:
            await callback.bot.send_message(
                chat_id=other_id,
                text=closed_notice,
                reply_markup=get_main_menu_keyboard(),
            )
        except (TelegramForbiddenError, TelegramBadRequest):
            pass

        # Send deal confirmation question to both buyer and seller
        for participant_id in (active_chat.buyer_id, active_chat.seller_id):
            try:
                await callback.bot.send_message(
                    chat_id=participant_id,
                    text=deal_prompt,
                    reply_markup=deal_kb,
                )
            except (TelegramForbiddenError, TelegramBadRequest, TelegramAPIError) as exc:
                logger.debug("Could not send deal confirmation to %d: %s", participant_id, exc)

    await callback.answer("Чат закрыт")


@router.callback_query(F.data.startswith(CB_DEAL_CONFIRM))
async def handle_deal_confirm(
    callback: CallbackQuery,
    user: User,
    session: AsyncSession,
) -> None:
    """Handle user confirming that deal took place."""
    try:
        session_id = int(callback.data[len(CB_DEAL_CONFIRM) :])  # type: ignore[index]
    except (IndexError, ValueError):
        await callback.answer("Ошибка параметров", show_alert=True)
        return

    chat_repo = ChatRepository(session)
    success, chat_session, status = await chat_repo.confirm_deal_participant(
        session_id=session_id, user_id=user.id, confirmed=True
    )

    if not success or chat_session is None:
        if status == "already_answered":
            await callback.answer("Вы уже дали ответ по этой сделке.", show_alert=True)
            if isinstance(callback.message, Message):
                try:
                    await callback.message.edit_reply_markup(reply_markup=None)
                except TelegramBadRequest:
                    pass
            return
        await callback.answer("Сделка недоступна или завершена.", show_alert=True)
        return

    if status == "waiting_other":
        await callback.answer("Подтверждение принято!")
        if isinstance(callback.message, Message):
            try:
                await callback.message.edit_text(
                    text=(
                        "✅ <b>Вы подтвердили, что сделка состоялась.</b>\n\n"
                        "⏳ <i>Ожидаем подтверждения от второго участника сделки...</i>"
                    ),
                    reply_markup=None,
                )
            except TelegramBadRequest:
                pass
        return

    if status == "both_confirmed":
        await callback.answer("Сделка подтверждена!")
        if isinstance(callback.message, Message):
            try:
                await callback.message.edit_text(
                    text="✅ <b>Сделка успешно подтверждена обеими сторонами!</b>",
                    reply_markup=None,
                )
            except TelegramBadRequest:
                pass

        if callback.bot:
            # Notify seller
            try:
                await callback.bot.send_message(
                    chat_id=chat_session.seller_id,
                    text=(
                        "🎉 <b>Обе стороны подтвердили совершение сделки!</b>\n\n"
                        "Покупателю отправлено предложение оценить ваше обслуживание."
                    ),
                )
            except TelegramAPIError:
                pass

            # Prompt buyer for rating
            try:
                await callback.bot.send_message(
                    chat_id=chat_session.buyer_id,
                    text=(
                        "🎉 <b>Обе стороны подтвердили совершение сделки!</b>\n\n"
                        "⭐️ <b>Пожалуйста, оцените продавца:</b>\n"
                        "Ваша оценка влияет на рейтинг продавца в каталоге Standoff 2."
                    ),
                    reply_markup=get_rating_keyboard(
                        session_id=chat_session.id,
                        seller_id=chat_session.seller_id,
                    ),
                )
            except TelegramAPIError:
                pass


@router.callback_query(F.data.startswith(CB_DEAL_REJECT))
async def handle_deal_reject(
    callback: CallbackQuery,
    user: User,
    session: AsyncSession,
) -> None:
    """Handle user stating that deal did NOT take place."""
    try:
        session_id = int(callback.data[len(CB_DEAL_REJECT) :])  # type: ignore[index]
    except (IndexError, ValueError):
        await callback.answer("Ошибка параметров", show_alert=True)
        return

    chat_repo = ChatRepository(session)
    success, chat_session, status = await chat_repo.confirm_deal_participant(
        session_id=session_id, user_id=user.id, confirmed=False
    )

    if not success or chat_session is None:
        if status == "already_answered":
            await callback.answer("Вы уже дали ответ по этой сделке.", show_alert=True)
            if isinstance(callback.message, Message):
                try:
                    await callback.message.edit_reply_markup(reply_markup=None)
                except TelegramBadRequest:
                    pass
            return
        await callback.answer("Сделка недоступна или завершена.", show_alert=True)
        return

    await callback.answer("Ответ зафиксирован")
    if isinstance(callback.message, Message):
        try:
            await callback.message.edit_text(
                text=(
                    "❌ <b>Вы отметили, что сделка не состоялась.</b>\n\n"
                    "Возможность оставить отзыв отключена."
                ),
                reply_markup=None,
            )
        except TelegramBadRequest:
            pass

    other_id = (
        chat_session.seller_id
        if user.id == chat_session.buyer_id
        else chat_session.buyer_id
    )
    if callback.bot:
        try:
            await callback.bot.send_message(
                chat_id=other_id,
                text=(
                    "ℹ️ <b>Один из участников отметил, что сделка не состоялась.</b>\n"
                    "Оценка продавца отменена."
                ),
            )
        except TelegramAPIError:
            pass




@router.callback_query(F.data.startswith(CB_RATE_SELLER))
async def handle_seller_rating(
    callback: CallbackQuery, user: User, session: AsyncSession
) -> None:
    """Process buyer's 1-5 star rating and update seller's average score."""
    parts = callback.data.split(":")  # type: ignore[union-attr]
    if len(parts) != 4:
        await callback.answer("Ошибка данных отзыва", show_alert=True)
        return

    try:
        session_id = int(parts[1])
        seller_id = int(parts[2])
        score = int(parts[3])
    except ValueError:
        await callback.answer("Некорректный формат оценки", show_alert=True)
        return

    chat_repo = ChatRepository(session)
    success, new_rating = await chat_repo.record_seller_review(
        session_id=session_id,
        seller_id=seller_id,
        buyer_id=user.id,
        score=score,
    )

    if not success:
        await callback.answer(
            "Вы уже оценивали этого продавца ранее. Одному продавцу разрешено ставить оценку не более 1 раза.",
            show_alert=True,
        )
        if isinstance(callback.message, Message):
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except TelegramBadRequest:
                pass
        return

    response_text = (
        "⭐️ <b>Спасибо за ваш отзыв!</b>\n\n"
        f"Вы поставили продавцу: <b>{score} из 5 ⭐️</b>.\n"
        f"Обновленный рейтинг продавца: <b>{new_rating:.1f} ⭐️</b>."
    )

    if isinstance(callback.message, Message):
        try:
            await callback.message.edit_text(
                text=response_text,
                reply_markup=None,
            )
        except TelegramBadRequest:
            pass

    await callback.answer("Оценка принята!")

