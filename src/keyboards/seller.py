from urllib.parse import quote

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.data.game_catalog import (
    get_categories,
    get_skins,
    get_sticker_rarities,
    get_weapons,
)

CB_CATEGORY: str = "sel_cat:"
CB_WEAPON: str = "sel_wpn:"
CB_SKIN: str = "sel_skn:"
CB_STICKER_COUNT: str = "sel_cnt:"
CB_STICKER_RARITY: str = "sel_rar:"
CB_PROMO: str = "promo:"
CB_FREE_BOOST: str = "free_boost_ad:"
CB_BOOST_AD: str = "boost_ad:"
CB_DELETE_AD: str = "del_ad:"
CB_CONFIRM_DELETE: str = "cf_del:"
CB_CANCEL_FSM: str = "fsm_cancel"
CB_BACK_CATEGORY: str = "fsm_b_cat"
CB_BACK_WEAPON: str = "fsm_b_wpn"
CB_BACK_SKIN: str = "fsm_b_skn"
CB_BACK_COUNT: str = "fsm_b_cnt"
CB_BACK_RARITY: str = "fsm_b_rar"


def get_cancel_fsm_keyboard() -> InlineKeyboardMarkup:
    """Build inline keyboard with single cancel button for FSM wizard steps."""
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data=CB_CANCEL_FSM, style="danger")
    return builder.as_markup()


def get_categories_keyboard() -> InlineKeyboardMarkup:
    """Build keyboard for category selection."""
    builder = InlineKeyboardBuilder()
    for idx, cat in enumerate(get_categories()):
        cb_data = f"{CB_CATEGORY}{cat}"
        if len(cb_data.encode("utf-8")) > 55:
            cb_data = f"{CB_CATEGORY}i:{idx}"
        builder.button(text=cat, callback_data=cb_data)
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data=CB_CANCEL_FSM))
    return builder.as_markup()


def get_weapons_keyboard(category: str) -> InlineKeyboardMarkup:
    """Build keyboard for weapon models in a category."""
    builder = InlineKeyboardBuilder()
    for idx, weapon in enumerate(get_weapons(category)):
        cb_data = f"{CB_WEAPON}{weapon}"
        if len(cb_data.encode("utf-8")) > 55:
            cb_data = f"{CB_WEAPON}i:{idx}"
        builder.button(text=weapon, callback_data=cb_data)
    builder.adjust(3)
    builder.row(
        InlineKeyboardButton(
            text="⬅️ Назад к категориям", callback_data=CB_BACK_CATEGORY
        ),
        InlineKeyboardButton(text="❌ Отмена", callback_data=CB_CANCEL_FSM),
    )
    return builder.as_markup()


def get_skins_keyboard(category: str, weapon: str) -> InlineKeyboardMarkup:
    """Build keyboard for skins of a specific weapon."""
    builder = InlineKeyboardBuilder()
    for idx, skin in enumerate(get_skins(category, weapon)):
        cb_data = f"{CB_SKIN}{skin}"
        if len(cb_data.encode("utf-8")) > 55:
            cb_data = f"{CB_SKIN}i:{idx}"
        builder.button(text=skin, callback_data=cb_data)
    builder.adjust(2)
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад к оружию", callback_data=CB_BACK_WEAPON),
        InlineKeyboardButton(text="❌ Отмена", callback_data=CB_CANCEL_FSM),
    )
    return builder.as_markup()


def get_sticker_counts_keyboard() -> InlineKeyboardMarkup:
    """Build keyboard for selecting sticker count (0-4)."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🚫 Без наклеек", callback_data=f"{CB_STICKER_COUNT}0"
        )
    )
    for count in (1, 2, 3, 4):
        builder.button(text=f"{count} шт.", callback_data=f"{CB_STICKER_COUNT}{count}")
    builder.adjust(1, 4)
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад к скинам", callback_data=CB_BACK_SKIN),
        InlineKeyboardButton(text="❌ Отмена", callback_data=CB_CANCEL_FSM),
    )
    return builder.as_markup()


def get_sticker_rarities_keyboard() -> InlineKeyboardMarkup:
    """Build keyboard for sticker rarity selection."""
    builder = InlineKeyboardBuilder()
    for rarity in get_sticker_rarities():
        builder.button(text=rarity, callback_data=f"{CB_STICKER_RARITY}{rarity}")
    builder.adjust(2)
    builder.row(
        InlineKeyboardButton(
            text="⬅️ Назад к числу наклеек", callback_data=CB_BACK_COUNT
        ),
        InlineKeyboardButton(text="❌ Отмена", callback_data=CB_CANCEL_FSM),
    )
    return builder.as_markup()


def get_price_input_keyboard(has_stickers: bool = True) -> InlineKeyboardMarkup:
    """Build keyboard for price input step."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="⬅️ Назад к наклейкам", callback_data=CB_BACK_COUNT
        ),
        InlineKeyboardButton(text="❌ Отмена", callback_data=CB_CANCEL_FSM),
    )
    return builder.as_markup()


def get_promotion_keyboard(
    ad_id: int,
    turbo_stars: int,
    standard_stars: int,
    is_boost: bool = False,
    is_admin: bool = False,
    free_boosts: int = 0,
    referrals_threshold: int = 3,
) -> InlineKeyboardMarkup:
    """Build keyboard for ad promotion tariff selection with valid Telegram styles."""
    builder = InlineKeyboardBuilder()
    if free_boosts > 0:
        builder.button(
            text=f"🎁 Применить бесплатный ТОП (Доступно: {free_boosts})",
            callback_data=f"{CB_PROMO}free_boost:{ad_id}",
            style="primary",
        )
    builder.button(
        text=f"🔥 «Турбо» (3 дня ТОП + 🔥) — {turbo_stars} ⭐️",
        callback_data=f"{CB_PROMO}turbo:{ad_id}",
        style="success",
    )
    builder.button(
        text=f"⚡️ «Стандарт» (24ч ТОП) — {standard_stars} ⭐️",
        callback_data=f"{CB_PROMO}standard:{ad_id}",
        style="success",
    )
    builder.button(
        text=f"🎁 Получить ТОП бесплатно за {referrals_threshold} друзей",
        callback_data=f"{CB_PROMO}ref_info:{ad_id}",
    )
    if is_admin:
        builder.button(
            text="🧪 Тест: ТОП бесплатно (Admin Dev)",
            callback_data=f"{CB_PROMO}test:{ad_id}",
            style="primary",
        )
    if not is_boost:
        builder.button(
            text="⏩ Опубликовать бесплатно",
            callback_data=f"{CB_PROMO}skip:{ad_id}",
        )
        builder.button(
            text="⬅️ Изменить цену (предыдущий пункт)",
            callback_data=f"{CB_PROMO}edit_price:{ad_id}",
        )
    else:
        builder.button(
            text="❌ Отмена",
            callback_data=f"{CB_PROMO}cancel:{ad_id}",
            style="danger",
        )
    builder.adjust(1)
    return builder.as_markup()


def get_referral_share_keyboard(
    invite_url: str,
    share_text: str,
    back_callback: str,
) -> InlineKeyboardMarkup:
    """Build keyboard with Telegram share URL button and back button."""
    builder = InlineKeyboardBuilder()
    share_href = f"https://t.me/share/url?url={quote(invite_url)}&text={quote(share_text)}"
    builder.button(text="📤 Поделиться с друзьями", url=share_href)
    builder.button(text="⬅️ Назад", callback_data=back_callback)
    builder.adjust(1)
    return builder.as_markup()


def get_edit_price_keyboard(ad_id: int) -> InlineKeyboardMarkup:
    """Build keyboard for editing price after ad creation."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="⬅️ Оставить прежнюю цену",
        callback_data=f"{CB_PROMO}cancel_edit:{ad_id}",
    )
    builder.button(
        text="🗑 Создать объявление заново",
        callback_data=f"{CB_PROMO}recreate:{ad_id}",
        style="danger",
    )
    builder.adjust(1)
    return builder.as_markup()


def get_seller_ad_keyboard(
    ad_id: int, is_top: bool, free_boosts: int = 0
) -> InlineKeyboardMarkup:
    """Build management keyboard for a seller's listing."""
    builder = InlineKeyboardBuilder()
    if not is_top:
        if free_boosts > 0:
            builder.button(
                text=f"⚡️ Поднять в ТОП бесплатно (Доступно: {free_boosts})",
                callback_data=f"{CB_FREE_BOOST}{ad_id}",
                style="success",
            )
        builder.button(
            text="🔥 Поднять в ТОП",
            callback_data=f"{CB_BOOST_AD}{ad_id}",
            style="primary",
        )
    builder.button(
        text="🗑 Удалить лот",
        callback_data=f"{CB_DELETE_AD}{ad_id}",
        style="danger",
    )
    builder.adjust(1)
    return builder.as_markup()


def get_delete_confirmation_keyboard(ad_id: int) -> InlineKeyboardMarkup:
    """Build confirmation keyboard before deleting an ad."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="⚠️ Да, удалить",
        callback_data=f"{CB_CONFIRM_DELETE}{ad_id}",
        style="danger",
    )
    builder.button(text="❌ Отмена", callback_data=f"cancel_del:{ad_id}")
    builder.adjust(2)
    return builder.as_markup()
