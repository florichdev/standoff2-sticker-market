from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

BTN_BUY_SKIN: str = "🛒 Купить скин"
BTN_SELL_SKIN: str = "💰 Продать скин"
BTN_MY_ADS: str = "📦 Мои объявления"
BTN_CHATS: str = "💬 Мои диалоги"
BTN_PROFILE: str = "👤 Профиль"
BTN_SETTINGS: str = "⚙️ Настройки"
BTN_HIDE_MENU: str = "🔽 Скрыть меню"

BTN_FEED_FILTER: str = "🔍 Настроить фильтр"
BTN_FEED_MORE: str = "📥 Загрузить ещё"
BTN_BACK_MENU: str = "🏠 Главное меню"


def get_main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Return collapsible reply keyboard for the main menu with valid Telegram styles."""
    from src.data.settings_manager import get_custom_ad_button

    custom_btn = get_custom_ad_button()
    custom_rows: list[list[KeyboardButton]] = []
    if custom_btn.get("enabled") and custom_btn.get("text"):
        custom_rows.append([KeyboardButton(text=custom_btn["text"].strip())])

    keyboard = [
        [
            KeyboardButton(text=BTN_BUY_SKIN, style="primary"),
            KeyboardButton(text=BTN_SELL_SKIN, style="success"),
        ],
        [
            KeyboardButton(text=BTN_CHATS, style="primary"),
            KeyboardButton(text=BTN_MY_ADS),
        ],
        [
            KeyboardButton(text=BTN_PROFILE),
            KeyboardButton(text=BTN_SETTINGS),
        ],
        *custom_rows,
        [
            KeyboardButton(text=BTN_HIDE_MENU),
        ],
    ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        is_persistent=False,
    )


def get_buyer_feed_reply_keyboard(has_more: bool = True) -> ReplyKeyboardMarkup:
    """Return pinned reply keyboard for buyer feed with instant filter button."""
    first_row = [
        KeyboardButton(text=BTN_FEED_FILTER, style="primary"),
    ]
    second_row = []
    if has_more:
        second_row.append(KeyboardButton(text=BTN_FEED_MORE, style="success"))
    second_row.append(KeyboardButton(text=BTN_BACK_MENU))

    keyboard = [
        first_row,
        second_row,
        [KeyboardButton(text=BTN_HIDE_MENU)],
    ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        is_persistent=False,
    )
