from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.data.game_catalog import (
    get_categories,
    get_skins,
    get_sticker_rarities,
    get_weapons,
)

CB_START_CHAT: str = "chat_start:"
CB_REPORT_AD: str = "rep_ad:"
CB_OPEN_FILTER: str = "flt_open"
CB_RESET_FILTER: str = "flt_reset"
CB_SUBSCRIBE_FILTER: str = "flt_sub"
CB_FEED_NOOP: str = "feed_noop"
CB_FEED_MORE: str = "feed_more:"

CB_FILTER_CAT: str = "flt_c:"
CB_FILTER_WPN: str = "flt_w:"
CB_FILTER_SKN: str = "flt_s:"
CB_FILTER_CNT: str = "flt_cnt:"
CB_FILTER_RAR: str = "flt_r:"
CB_FILTER_SORT: str = "flt_srt:"
CB_FILTER_CANCEL: str = "flt_cancel"
CB_FILTER_BACK: str = "flt_b:"


def get_ad_card_keyboard(
    ad_id: int,
    current_index: int = 0,
    total_count: int = 0,
    is_filtered: bool = False,
    is_subscribed: bool = False,
) -> InlineKeyboardMarkup:
    """Build action buttons for an individual ad card message in the feed."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="💬 Написать продавцу",
            callback_data=f"{CB_START_CHAT}{ad_id}",
        ),
        InlineKeyboardButton(
            text="⚠️ Пожаловаться",
            callback_data=f"{CB_REPORT_AD}{ad_id}",
        ),
    )
    return builder.as_markup()


def get_feed_controls_keyboard(
    next_offset: int | None = None,
    is_filtered: bool = False,
    is_subscribed: bool = False,
) -> InlineKeyboardMarkup:
    """Build bottom controls keyboard for ads feed."""
    builder = InlineKeyboardBuilder()
    if next_offset is not None:
        builder.row(
            InlineKeyboardButton(
                text="📥 Загрузить ещё объявления",
                callback_data=f"{CB_FEED_MORE}{next_offset}",
            )
        )
    row = [InlineKeyboardButton(text="🔍 Настроить фильтр", callback_data=CB_OPEN_FILTER)]
    if is_filtered:
        row.append(
            InlineKeyboardButton(text="🔄 Сбросить фильтр", callback_data=CB_RESET_FILTER)
        )
    builder.row(*row)
    if is_filtered and not is_subscribed:
        builder.row(
            InlineKeyboardButton(
                text="🔔 Включить уведомления",
                callback_data=CB_SUBSCRIBE_FILTER,
            )
        )
    return builder.as_markup()


def get_empty_feed_keyboard() -> InlineKeyboardMarkup:
    """Build keyboard when the general feed is empty."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🔍 Настроить фильтр",
            callback_data=CB_OPEN_FILTER,
        )
    )
    return builder.as_markup()


def get_empty_filtered_feed_keyboard(
    is_subscribed: bool = False,
) -> InlineKeyboardMarkup:
    """Build keyboard when no results match active filter."""
    builder = InlineKeyboardBuilder()
    if not is_subscribed:
        builder.row(
            InlineKeyboardButton(
                text="🔔 Включить уведомления",
                callback_data=CB_SUBSCRIBE_FILTER,
            )
        )
    builder.row(
        InlineKeyboardButton(text="🔍 Изменить фильтр", callback_data=CB_OPEN_FILTER),
        InlineKeyboardButton(
            text="🔄 Сбросить фильтр",
            callback_data=CB_RESET_FILTER,
        ),
    )
    return builder.as_markup()


def get_filter_categories_keyboard() -> InlineKeyboardMarkup:
    """Build category selection keyboard for smart filter."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🌐 Любая категория", callback_data=f"{CB_FILTER_CAT}any"
        )
    )
    for idx, cat in enumerate(get_categories()):
        cb_data = f"{CB_FILTER_CAT}{cat}"
        if len(cb_data.encode("utf-8")) > 55:
            cb_data = f"{CB_FILTER_CAT}i:{idx}"
        builder.button(text=cat, callback_data=cb_data)
    builder.adjust(1, 2)
    builder.row(
        InlineKeyboardButton(
            text="❌ Отмена",
            callback_data=CB_FILTER_CANCEL,
        )
    )
    return builder.as_markup()


def get_filter_weapons_keyboard(category: str | None) -> InlineKeyboardMarkup:
    """Build weapon model selection keyboard for filter."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🌐 Любая модель", callback_data=f"{CB_FILTER_WPN}any"
        )
    )
    if category and category != "any":
        weapons = get_weapons(category)
        for idx, wpn in enumerate(weapons):
            cb_data = f"{CB_FILTER_WPN}{wpn}"
            if len(cb_data.encode("utf-8")) > 55:
                cb_data = f"{CB_FILTER_WPN}i:{idx}"
            builder.button(text=wpn, callback_data=cb_data)
        builder.adjust(1, 3)
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CB_FILTER_BACK}category")
    )
    return builder.as_markup()


def get_filter_skins_keyboard(
    category: str | None, weapon: str | None
) -> InlineKeyboardMarkup:
    """Build skins selection keyboard for filter."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🌐 Любой скин", callback_data=f"{CB_FILTER_SKN}any")
    )
    if category and weapon and category != "any" and weapon != "any":
        skins = get_skins(category, weapon)
        for idx, skin in enumerate(skins):
            cb_data = f"{CB_FILTER_SKN}{skin}"
            if len(cb_data.encode("utf-8")) > 55:
                cb_data = f"{CB_FILTER_SKN}i:{idx}"
            builder.button(text=skin, callback_data=cb_data)
        builder.adjust(1, 2)
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CB_FILTER_BACK}weapon")
    )
    return builder.as_markup()


def get_filter_sticker_counts_keyboard() -> InlineKeyboardMarkup:
    """Build sticker count selection keyboard for filter."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🌐 Любое количество", callback_data=f"{CB_FILTER_CNT}any"
        ),
        InlineKeyboardButton(text="🚫 Без наклеек", callback_data=f"{CB_FILTER_CNT}0"),
    )
    for count in (1, 2, 3, 4):
        builder.button(text=f"{count} шт.", callback_data=f"{CB_FILTER_CNT}{count}")
    builder.adjust(2, 4)
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CB_FILTER_BACK}skin")
    )
    return builder.as_markup()


def get_filter_sticker_rarities_keyboard() -> InlineKeyboardMarkup:
    """Build sticker rarity selection keyboard for filter."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🌐 Любая редкость", callback_data=f"{CB_FILTER_RAR}any"
        )
    )
    for rarity in get_sticker_rarities():
        builder.button(text=rarity, callback_data=f"{CB_FILTER_RAR}{rarity}")
    builder.adjust(1, 2)
    builder.row(
        InlineKeyboardButton(
            text="⬅️ Назад", callback_data=f"{CB_FILTER_BACK}sticker_count"
        )
    )
    return builder.as_markup()


def get_filter_sort_keyboard() -> InlineKeyboardMarkup:
    """Build price sorting option keyboard."""
    builder = InlineKeyboardBuilder()
    builder.button(text="📈 По возрастанию цены", callback_data=f"{CB_FILTER_SORT}asc")
    builder.button(text="📉 По убыванию цены", callback_data=f"{CB_FILTER_SORT}desc")
    builder.button(
        text="⚡️ Сначала свежие (по умолчанию)",
        callback_data=f"{CB_FILTER_SORT}default",
    )
    builder.adjust(1)
    builder.row(
        InlineKeyboardButton(
            text="⬅️ Назад", callback_data=f"{CB_FILTER_BACK}sticker_count"
        )
    )
    return builder.as_markup()
