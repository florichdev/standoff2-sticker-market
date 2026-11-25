import asyncio
import html
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.game_catalog import (
    get_categories,
    get_skins,
    get_weapons,
    is_valid_selection,
    is_valid_sticker_rarity,
)
from src.database.models import Ad, User
from src.database.repositories.ad_repo import AdRepository
from src.database.repositories.subscription_repo import SubscriptionRepository
from src.keyboards.buyer import (
    CB_FEED_MORE,
    CB_FEED_NOOP,
    CB_FILTER_BACK,
    CB_FILTER_CANCEL,
    CB_FILTER_CAT,
    CB_FILTER_CNT,
    CB_FILTER_RAR,
    CB_FILTER_SKN,
    CB_FILTER_SORT,
    CB_FILTER_WPN,
    CB_OPEN_FILTER,
    CB_RESET_FILTER,
    CB_SUBSCRIBE_FILTER,
    get_ad_card_keyboard,
    get_empty_feed_keyboard,
    get_empty_filtered_feed_keyboard,
    get_feed_controls_keyboard,
    get_filter_categories_keyboard,
    get_filter_skins_keyboard,
    get_filter_sort_keyboard,
    get_filter_sticker_counts_keyboard,
    get_filter_sticker_rarities_keyboard,
    get_filter_weapons_keyboard,
)
from src.keyboards.main_menu import (
    BTN_BUY_SKIN,
    BTN_FEED_FILTER,
    BTN_FEED_MORE,
    get_buyer_feed_reply_keyboard,
    get_main_menu_keyboard,
)
from src.states.buyer_filter import BuyerFilterStates
from src.utils.formatters import format_ad_caption
from src.utils.helpers import safe_send_photo

logger = logging.getLogger(__name__)
router = Router(name="buyer")


def format_buyer_ad_caption(ad: Ad) -> str:
    """Format rich text caption for an ad card in buyer feed."""
    return format_ad_caption(ad, mode="buyer")


async def edit_filter_step(
    callback: CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup
) -> None:
    """Safely edit text and inline keyboard for a filter wizard step."""
    if isinstance(callback.message, Message):
        try:
            await callback.message.edit_text(
                text=text, reply_markup=reply_markup, parse_mode="HTML"
            )
        except TelegramBadRequest as e:
            if "message is not modified" in str(e).lower():
                return
            await callback.message.answer(
                text=text, reply_markup=reply_markup, parse_mode="HTML"
            )




PAGE_SIZE: int = 10


async def render_ads_feed(
    bot: Bot,
    chat_id: int,
    session: AsyncSession,
    state: FSMContext,
    offset: int = 0,
    is_filtered: bool = False,
) -> None:
    """Stream active ads sequentially as individual photo messages without pagination switchers."""
    filter_data = await state.get_data() if is_filtered else {}
    category = filter_data.get("category")
    weapon_model = filter_data.get("weapon")
    skin_name = filter_data.get("skin")
    sticker_count = filter_data.get("sticker_count")
    price_sort = filter_data.get("price_sort")
    is_subscribed = bool(filter_data.get("is_subscribed", False))

    ad_repo = AdRepository(session)
    ads, total_count = await ad_repo.get_feed_ads(
        offset=offset,
        limit=PAGE_SIZE,
        category=category,
        weapon_model=weapon_model,
        skin_name=skin_name,
        sticker_count=sticker_count,
        price_sort=price_sort,
    )

    if total_count == 0 or not ads:
        if is_filtered:
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    "🔍 <b>По вашему фильтру пока нет подходящих лотов</b> 😔\n\n"
                    "💡 <b>Включите уведомления</b>, чтобы бот моментально прислал "
                    "сообщение, как только продавец выставит такой скин!"
                ),
                reply_markup=get_empty_filtered_feed_keyboard(is_subscribed=is_subscribed),
                parse_mode="HTML",
            )
        else:
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    "🛒 <b>Лента объявлений пуста</b>\n\n"
                    "В данный момент нет активных предложений.\n"
                    "Вы можете настроить умный фильтр и подписаться на уведомления!"
                ),
                reply_markup=get_empty_feed_keyboard(),
                parse_mode="HTML",
            )
        return

    ads_to_render = list(reversed(ads))
    rendered_ids = [ad.id for ad in ads_to_render]
    await ad_repo.increment_multiple_ad_views(rendered_ids)

    for ad in ads_to_render:
        caption = format_buyer_ad_caption(ad)
        try:
            await safe_send_photo(
                bot=bot,
                chat_id=chat_id,
                photo=ad.photo_file_id,
                caption=caption,
                reply_markup=get_ad_card_keyboard(ad.id),
                parse_mode="HTML",
                ad_id=ad.id,
            )
        except TelegramRetryAfter as exc:
            logger.warning(
                "Rate limit hit during feed render in chat %s. Waiting %d seconds...",
                chat_id,
                exc.retry_after,
            )
            await asyncio.sleep(exc.retry_after + 1)
            await safe_send_photo(
                bot=bot,
                chat_id=chat_id,
                photo=ad.photo_file_id,
                caption=caption,
                reply_markup=get_ad_card_keyboard(ad.id),
                parse_mode="HTML",
                ad_id=ad.id,
            )
        await asyncio.sleep(0.35)

    next_offset = offset + len(ads)
    has_more = next_offset < total_count

    await state.update_data(feed_offset=next_offset)

    if has_more:
        remaining = total_count - next_offset
        summary_text = (
            f"📊 Показано <b>{next_offset} из {total_count}</b> объявлений.\n"
            f"Осталось ещё: <b>{remaining}</b>."
        )
    else:
        summary_text = f"🏁 Вы просмотрели все актуальные объявления ({total_count} шт.)."

    await bot.send_message(
        chat_id=chat_id,
        text=summary_text,
        reply_markup=get_feed_controls_keyboard(
            next_offset=next_offset if has_more else None,
            is_filtered=is_filtered,
            is_subscribed=is_subscribed,
        ),
        parse_mode="HTML",
    )


@router.message(F.text == BTN_BUY_SKIN)
@router.message(Command("buy"))
async def open_general_feed(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    """Open general feed, activate pinned filter keyboard, and stream ads."""
    await state.clear()
    await message.answer(
        "🛒 <b>Витрина скинов Standoff 2</b>\nЗагружаем актуальные предложения...",
        reply_markup=get_buyer_feed_reply_keyboard(has_more=True),
        parse_mode="HTML",
    )
    if message.bot:
        await render_ads_feed(
            bot=message.bot,
            chat_id=message.chat.id,
            session=session,
            state=state,
            offset=0,
            is_filtered=False,
        )


@router.message(F.text == BTN_FEED_FILTER)
@router.message(Command("filter"))
async def handle_open_filter_button(message: Message, state: FSMContext) -> None:
    """Initiate smart filter configuration from reply keyboard button."""
    await state.set_state(BuyerFilterStates.waiting_for_category)
    text = (
        "🔍 <b>Умный фильтр скинов</b>\n\n"
        "Шаг 1 из 5: Выберите категорию оружия или нажмите «Любая категория»:"
    )
    await message.answer(
        text=text,
        reply_markup=get_filter_categories_keyboard(),
        parse_mode="HTML",
    )


@router.message(F.text == BTN_FEED_MORE)
async def handle_feed_more_button(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    """Load next batch of ads from reply keyboard button."""
    data = await state.get_data()
    offset = data.get("feed_offset", 0)
    is_filtered = bool(
        data.get("category")
        or data.get("weapon")
        or data.get("skin")
        or data.get("sticker_count") is not None
        or data.get("price_sort")
    )
    if message.bot:
        await render_ads_feed(
            bot=message.bot,
            chat_id=message.chat.id,
            session=session,
            state=state,
            offset=offset,
            is_filtered=is_filtered,
        )


@router.callback_query(F.data.startswith(CB_FEED_MORE))
async def handle_feed_more(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    """Load next batch of ads in sequence."""
    try:
        offset = int(callback.data[len(CB_FEED_MORE) :])  # type: ignore[index]
    except (IndexError, ValueError):
        await callback.answer("Ошибка запроса", show_alert=True)
        return

    filter_data = await state.get_data()
    is_filtered = bool(
        filter_data.get("category")
        or filter_data.get("weapon")
        or filter_data.get("skin")
        or filter_data.get("sticker_count") is not None
        or filter_data.get("price_sort")
    )

    if isinstance(callback.message, Message):
        chat_id = callback.message.chat.id
    else:
        chat_id = callback.from_user.id

    if callback.bot:
        await render_ads_feed(
            bot=callback.bot,
            chat_id=chat_id,
            session=session,
            state=state,
            offset=offset,
            is_filtered=is_filtered,
        )
    await callback.answer()


@router.callback_query(F.data == CB_FEED_NOOP)
async def feed_noop(callback: CallbackQuery) -> None:
    """No-op callback for non-interactive indicators."""
    await callback.answer()




@router.callback_query(F.data == CB_OPEN_FILTER)
async def start_filter_wizard(callback: CallbackQuery, state: FSMContext) -> None:
    """Initiate smart filter configuration."""
    await state.set_state(BuyerFilterStates.waiting_for_category)
    text = (
        "🔍 <b>Умный фильтр скинов</b>\n\n"
        "Шаг 1 из 5: Выберите категорию оружия или нажмите «Любая категория»:"
    )
    keyboard = get_filter_categories_keyboard()

    if isinstance(callback.message, Message):
        if callback.message.photo:
            await callback.message.delete()
            if callback.bot:
                await callback.bot.send_message(
                    chat_id=callback.message.chat.id,
                    text=text,
                    reply_markup=keyboard,
                    parse_mode="HTML",
                )
        else:
            await callback.message.edit_text(
                text=text, reply_markup=keyboard, parse_mode="HTML"
            )
    await callback.answer()


@router.callback_query(F.data == CB_FILTER_CANCEL)
async def cancel_filter(callback: CallbackQuery, state: FSMContext) -> None:
    """Cancel filter configurator and return to main menu."""
    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.edit_text("❌ Настройка фильтра отменена.")
    await callback.answer("Отменено")


@router.callback_query(
    BuyerFilterStates.waiting_for_category, F.data.startswith(CB_FILTER_CAT)
)
async def filter_process_category(callback: CallbackQuery, state: FSMContext) -> None:
    """Save selected category and prompt for weapon model."""
    category_val = callback.data[len(CB_FILTER_CAT) :]  # type: ignore[index]
    if category_val == "any":
        category = None
    elif category_val.startswith("i:"):
        try:
            idx = int(category_val[2:])
            category = get_categories()[idx]
        except (IndexError, ValueError):
            await callback.answer("Некорректная категория", show_alert=True)
            return
    else:
        category = category_val

    if category and category not in get_categories():
        await callback.answer("Некорректная категория", show_alert=True)
        return

    await state.update_data(category=category)
    await state.set_state(BuyerFilterStates.waiting_for_weapon)

    cat_label = html.escape(category if category else "Любая")
    text = (
        f"📂 Категория: <b>{cat_label}</b>\n\n🔫 <b>Шаг 2 из 5: Выберите модель оружия</b>:"
    )
    await edit_filter_step(callback, text, get_filter_weapons_keyboard(category))
    await callback.answer()


@router.callback_query(
    BuyerFilterStates.waiting_for_weapon, F.data == f"{CB_FILTER_BACK}category"
)
async def filter_back_to_category(callback: CallbackQuery, state: FSMContext) -> None:
    """Navigate back to category selection."""
    await state.set_state(BuyerFilterStates.waiting_for_category)
    await edit_filter_step(
        callback,
        "🔍 <b>Умный фильтр</b>\n\nШаг 1 из 5: Выберите категорию оружия:",
        get_filter_categories_keyboard(),
    )
    await callback.answer()


@router.callback_query(
    BuyerFilterStates.waiting_for_weapon, F.data.startswith(CB_FILTER_WPN)
)
async def filter_process_weapon(callback: CallbackQuery, state: FSMContext) -> None:
    """Save selected weapon model and prompt for skin."""
    data = await state.get_data()
    category = data.get("category")
    weapon_val = callback.data[len(CB_FILTER_WPN) :]  # type: ignore[index]
    if weapon_val == "any":
        weapon = None
    elif weapon_val.startswith("i:"):
        try:
            idx = int(weapon_val[2:])
            weapons = get_weapons(category) if category else []
            weapon = weapons[idx]
        except (IndexError, ValueError):
            await callback.answer("Некорректная модель оружия", show_alert=True)
            return
    else:
        weapon = weapon_val

    if category and weapon and weapon not in get_weapons(category):
        await callback.answer("Некорректная модель оружия", show_alert=True)
        return

    await state.update_data(weapon=weapon)
    await state.set_state(BuyerFilterStates.waiting_for_skin)

    wpn_label = html.escape(weapon if weapon else "Любая")
    text = f"🔫 Оружие: <b>{wpn_label}</b>\n\n🎨 <b>Шаг 3 из 5: Выберите скин</b>:"
    await edit_filter_step(callback, text, get_filter_skins_keyboard(category, weapon))
    await callback.answer()


@router.callback_query(
    BuyerFilterStates.waiting_for_skin, F.data == f"{CB_FILTER_BACK}weapon"
)
async def filter_back_to_weapon(callback: CallbackQuery, state: FSMContext) -> None:
    """Navigate back to weapon selection."""
    data = await state.get_data()
    category = data.get("category")
    await state.set_state(BuyerFilterStates.waiting_for_weapon)
    cat_label = html.escape(category if category else "Любая")
    text = (
        f"📂 Категория: <b>{cat_label}</b>\n\n🔫 <b>Шаг 2 из 5: Выберите модель оружия</b>:"
    )
    await edit_filter_step(callback, text, get_filter_weapons_keyboard(category))
    await callback.answer()


@router.callback_query(
    BuyerFilterStates.waiting_for_skin, F.data.startswith(CB_FILTER_SKN)
)
async def filter_process_skin(callback: CallbackQuery, state: FSMContext) -> None:
    """Save selected skin and prompt for sticker count."""
    data = await state.get_data()
    category = data.get("category")
    weapon = data.get("weapon")
    skin_val = callback.data[len(CB_FILTER_SKN) :]  # type: ignore[index]
    if skin_val == "any":
        skin = None
    elif skin_val.startswith("i:"):
        try:
            idx = int(skin_val[2:])
            skins = get_skins(category, weapon) if category and weapon else []
            skin = skins[idx]
        except (IndexError, ValueError):
            await callback.answer("Некорректный скин", show_alert=True)
            return
    else:
        skin = skin_val

    if category and weapon and skin and not is_valid_selection(category, weapon, skin):
        await callback.answer("Некорректный скин", show_alert=True)
        return

    await state.update_data(skin=skin)
    await state.set_state(BuyerFilterStates.waiting_for_sticker_count)

    skin_label = html.escape(skin if skin else "Любой")
    text = (
        f"🎨 Скин: <b>{skin_label}</b>\n\n🏷 <b>Шаг 4 из 5: Выберите количество наклеек</b>:"
    )
    await edit_filter_step(callback, text, get_filter_sticker_counts_keyboard())
    await callback.answer()


@router.callback_query(
    BuyerFilterStates.waiting_for_sticker_count, F.data == f"{CB_FILTER_BACK}skin"
)
async def filter_back_to_skin(callback: CallbackQuery, state: FSMContext) -> None:
    """Navigate back to skin selection."""
    data = await state.get_data()
    category = data.get("category")
    weapon = data.get("weapon")
    await state.set_state(BuyerFilterStates.waiting_for_skin)
    wpn_label = html.escape(weapon if weapon else "Любая")
    text = f"🔫 Оружие: <b>{wpn_label}</b>\n\n🎨 <b>Шаг 3 из 5: Выберите скин</b>:"
    await edit_filter_step(callback, text, get_filter_skins_keyboard(category, weapon))
    await callback.answer()


@router.callback_query(
    BuyerFilterStates.waiting_for_sticker_count, F.data.startswith(CB_FILTER_CNT)
)
async def filter_process_sticker_count(
    callback: CallbackQuery, state: FSMContext
) -> None:
    """Save sticker count and proceed directly to price sort (skipping rarity)."""
    count_val = callback.data[len(CB_FILTER_CNT) :]  # type: ignore[index]
    sticker_count: int | None = None
    if count_val != "any":
        try:
            sticker_count = int(count_val)
            if sticker_count < 0 or sticker_count > 4:
                raise ValueError
        except ValueError:
            await callback.answer("Некорректное число наклеек", show_alert=True)
            return

    await state.update_data(sticker_count=sticker_count, sticker_rarity=None)
    await state.set_state(BuyerFilterStates.waiting_for_price_sort)

    count_label = f"{sticker_count} шт." if sticker_count is not None else "Любое"
    if sticker_count == 0:
        count_label = "Без наклеек"
    text = (
        f"🏷 Наклеек: <b>{count_label}</b>\n\n"
        "📊 <b>Шаг 5 из 5: Выберите сортировку по цене</b>:"
    )
    await edit_filter_step(callback, text, get_filter_sort_keyboard())
    await callback.answer()


@router.callback_query(
    BuyerFilterStates.waiting_for_price_sort,
    F.data.in_({f"{CB_FILTER_BACK}sticker_count", f"{CB_FILTER_BACK}sticker_rarity"}),
)
async def filter_back_to_sticker_count(
    callback: CallbackQuery, state: FSMContext
) -> None:
    """Navigate back to sticker count selection."""
    await state.set_state(BuyerFilterStates.waiting_for_sticker_count)
    await edit_filter_step(
        callback,
        "🏷 <b>Шаг 4 из 5: Выберите количество наклеек</b>:",
        get_filter_sticker_counts_keyboard(),
    )
    await callback.answer()


@router.callback_query(
    BuyerFilterStates.waiting_for_price_sort, F.data.startswith(CB_FILTER_SORT)
)
async def filter_process_sort(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    """Apply price sort, execute filtered query, and stream results as messages."""
    sort_val = callback.data[len(CB_FILTER_SORT) :]  # type: ignore[index]
    price_sort = sort_val if sort_val in ("asc", "desc") else None

    await state.update_data(price_sort=price_sort, is_subscribed=False)
    await state.set_state(BuyerFilterStates.viewing_filtered_feed)

    if isinstance(callback.message, Message):
        chat_id = callback.message.chat.id
        await callback.message.delete()
    else:
        chat_id = callback.from_user.id

    if callback.bot:
        await callback.bot.send_message(
            chat_id=chat_id,
            text="🔍 <b>Результаты поиска по фильтру:</b>",
            reply_markup=get_buyer_feed_reply_keyboard(has_more=True),
            parse_mode="HTML",
        )
        await render_ads_feed(
            bot=callback.bot,
            chat_id=chat_id,
            session=session,
            state=state,
            offset=0,
            is_filtered=True,
        )
    await callback.answer()




MAX_SEARCH_SUBSCRIPTIONS = 15


@router.callback_query(F.data == CB_SUBSCRIBE_FILTER)
async def subscribe_to_filter(
    callback: CallbackQuery, state: FSMContext, user: User, session: AsyncSession
) -> None:
    """Save current filter criteria to search subscriptions for instant push alerts."""
    sub_repo = SubscriptionRepository(session)
    existing_subs = await sub_repo.get_user_subscriptions(user.id)
    if len(existing_subs) >= MAX_SEARCH_SUBSCRIPTIONS:
        await callback.answer(
            f"⚠️ Достигнут лимит: максимум {MAX_SEARCH_SUBSCRIPTIONS} сохраненных фильтров.",
            show_alert=True,
        )
        return

    filter_data = await state.get_data()
    category = filter_data.get("category")
    weapon_model = filter_data.get("weapon")
    skin_name = filter_data.get("skin")
    sticker_count = filter_data.get("sticker_count")
    sticker_rarity = filter_data.get("sticker_rarity")

    await sub_repo.save_subscription(
        user_id=user.id,
        category=category,
        weapon_model=weapon_model,
        skin_name=skin_name,
        sticker_count=sticker_count,
        sticker_rarity=sticker_rarity,
    )

    await state.update_data(is_subscribed=True)

    await callback.answer(
        "🔔 Подписка сохранена! Мы пришлем уведомление, как только появится этот скин.",
        show_alert=True,
    )

    if isinstance(callback.message, Message):
        if not callback.message.photo:
            try:
                await callback.message.edit_reply_markup(
                    reply_markup=get_feed_controls_keyboard(
                        is_filtered=True,
                        is_subscribed=True,
                    )
                )
            except TelegramBadRequest:
                pass


@router.callback_query(F.data == CB_RESET_FILTER)
async def reset_filter(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    """Clear filter parameters and stream the main feed."""
    await state.clear()
    if isinstance(callback.message, Message):
        chat_id = callback.message.chat.id
        await callback.message.delete()
    else:
        chat_id = callback.from_user.id

    if callback.bot:
        await callback.bot.send_message(
            chat_id=chat_id,
            text="🔄 <b>Фильтр сброшен.</b> Загрузка всех предложений...",
            reply_markup=get_buyer_feed_reply_keyboard(has_more=True),
            parse_mode="HTML",
        )
        await render_ads_feed(
            bot=callback.bot,
            chat_id=chat_id,
            session=session,
            state=state,
            offset=0,
            is_filtered=False,
        )
    await callback.answer("Фильтр сброшен")


@router.message(
    StateFilter(
        BuyerFilterStates.waiting_for_category,
        BuyerFilterStates.waiting_for_weapon,
        BuyerFilterStates.waiting_for_skin,
        BuyerFilterStates.waiting_for_sticker_count,
        BuyerFilterStates.waiting_for_sticker_rarity,
        BuyerFilterStates.waiting_for_price_sort,
    ),
    Command("cancel"),
)
@router.message(
    StateFilter(
        BuyerFilterStates.waiting_for_category,
        BuyerFilterStates.waiting_for_weapon,
        BuyerFilterStates.waiting_for_skin,
        BuyerFilterStates.waiting_for_sticker_count,
        BuyerFilterStates.waiting_for_sticker_rarity,
        BuyerFilterStates.waiting_for_price_sort,
    ),
    F.text.in_({"❌ Отмена", "Отмена", "отмена"}),
)
async def filter_message_cancel(message: Message, state: FSMContext) -> None:
    """Handle text cancel commands during smart filter configuration."""
    await state.clear()
    await message.answer(
        "❌ Настройка фильтра отменена.",
        reply_markup=get_main_menu_keyboard(),
    )


@router.message(
    StateFilter(
        BuyerFilterStates.waiting_for_category,
        BuyerFilterStates.waiting_for_weapon,
        BuyerFilterStates.waiting_for_skin,
        BuyerFilterStates.waiting_for_sticker_count,
        BuyerFilterStates.waiting_for_sticker_rarity,
        BuyerFilterStates.waiting_for_price_sort,
    ),
    ~F.successful_payment,
)
async def filter_message_fallback(message: Message) -> None:
    """Prompt user to use inline buttons when sending unexpected messages during filtering."""
    await message.answer(
        "👆 Пожалуйста, используйте кнопки под сообщением для настройки фильтра "
        "или отправьте /cancel для отмены."
    )

