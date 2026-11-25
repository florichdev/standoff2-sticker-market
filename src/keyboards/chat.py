from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

CB_CHAT_CLOSE: str = "chat_cls:"
CB_CHAT_REPORT: str = "chat_rep:"
CB_RATE_SELLER: str = "rate_s:"
CB_CHAT_RESUME: str = "chat_res:"
CB_CHAT_PAUSE: str = "chat_pause:"
CB_DEAL_CONFIRM: str = "deal_ok:"
CB_DEAL_REJECT: str = "deal_no:"


def get_deal_confirmation_keyboard(session_id: int) -> InlineKeyboardMarkup:
    """Build deal confirmation buttons for buyer and seller."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ Да, сделка состоялась",
            callback_data=f"{CB_DEAL_CONFIRM}{session_id}",
            style="success",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="❌ Нет, сделка не состоялась",
            callback_data=f"{CB_DEAL_REJECT}{session_id}",
            style="danger",
        )
    )
    return builder.as_markup()


def get_chat_control_keyboard(session_id: int) -> InlineKeyboardMarkup:
    """Build action bar for an open proxy chat session."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="⏸ Свернуть диалог в меню",
            callback_data=f"{CB_CHAT_PAUSE}{session_id}",
            style="primary",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="❌ Завершить сделку",
            callback_data=f"{CB_CHAT_CLOSE}{session_id}",
            style="danger",
        ),
        InlineKeyboardButton(
            text="⚠️ Пожаловаться",
            callback_data=f"{CB_CHAT_REPORT}{session_id}",
        ),
    )
    return builder.as_markup()


def get_chat_incoming_keyboard(session_id: int) -> InlineKeyboardMarkup:
    """Build notification buttons for incoming message when chat is in background."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="💬 Ответить в чате",
            callback_data=f"{CB_CHAT_RESUME}{session_id}",
            style="primary",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="❌ Завершить сделку",
            callback_data=f"{CB_CHAT_CLOSE}{session_id}",
            style="danger",
        ),
        InlineKeyboardButton(
            text="⚠️ Пожаловаться",
            callback_data=f"{CB_CHAT_REPORT}{session_id}",
        ),
    )
    return builder.as_markup()


def get_rating_keyboard(session_id: int, seller_id: int) -> InlineKeyboardMarkup:
    """Build 1-5 star rating buttons for buyer after chat completion with valid Telegram styles."""
    builder = InlineKeyboardBuilder()
    for star in range(1, 6):
        star_style = "success" if star >= 4 else ("danger" if star <= 2 else "primary")
        builder.button(
            text=f"{star} ⭐️",
            callback_data=f"{CB_RATE_SELLER}{session_id}:{seller_id}:{star}",
            style=star_style,
        )
    builder.adjust(5)
    return builder.as_markup()


def get_my_chats_keyboard(
    chats: list[Any], current_user_id: int
) -> InlineKeyboardMarkup:
    """Build selection list of user's active dialogs."""
    builder = InlineKeyboardBuilder()
    for chat in chats:
        ad = chat.ad
        title = f"{ad.weapon_model} | {ad.skin_name}" if ad else f"Лот #{chat.ad_id}"
        role = "Продавец" if current_user_id == chat.seller_id else "Покупатель"
        builder.button(
            text=f"💬 {title} ({role})",
            callback_data=f"{CB_CHAT_RESUME}{chat.id}",
        )
    builder.adjust(1)
    return builder.as_markup()
