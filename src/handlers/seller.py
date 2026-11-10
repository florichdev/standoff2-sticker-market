import asyncio
from datetime import UTC, datetime
import logging
from typing import Any

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.data.game_catalog import (
    get_categories,
    get_weapons,
    is_valid_selection,
)
from src.data.settings_manager import (
    get_max_active_ads,
    get_referrals_for_top,
    get_stars_standard_price,
    get_stars_turbo_price,
)
from src.database.models import User
from src.database.repositories.ad_repo import AdRepository
from src.database.repositories.referral_repo import ReferralRepository
from src.database.repositories.subscription_repo import SubscriptionRepository
from src.keyboards.main_menu import BTN_MY_ADS, BTN_SELL_SKIN, get_main_menu_keyboard
from src.keyboards.seller import (
    CB_BACK_CATEGORY,
    CB_BACK_COUNT,
    CB_BACK_RARITY,
    CB_BACK_SKIN,
    CB_BACK_WEAPON,
    CB_BOOST_AD,
    CB_CANCEL_FSM,
    CB_CATEGORY,
    CB_CONFIRM_DELETE,
    CB_DELETE_AD,
    CB_FREE_BOOST,
    CB_PROMO,
    CB_SKIN,
    CB_STICKER_COUNT,
    CB_WEAPON,
    get_cancel_fsm_keyboard,
    get_categories_keyboard,
    get_delete_confirmation_keyboard,
    get_edit_price_keyboard,
    get_price_input_keyboard,
    get_promotion_keyboard,
    get_referral_share_keyboard,
    get_seller_ad_keyboard,
    get_skins_keyboard,
    get_sticker_counts_keyboard,
    get_weapons_keyboard,
)
from src.services.autopost import autopost_new_ad
from src.services.notification import notify_subscribers_about_new_ad
from src.states.ad_creation import AdCreationStates
from src.utils.formatters import format_ad_caption
from src.utils.helpers import safe_answer_photo, safe_edit_message

logger = logging.getLogger(__name__)
router = Router(name="seller")

_background_tasks: set[asyncio.Task[Any]] = set()




@router.message(F.text == BTN_SELL_SKIN)
@router.message(Command("sell"))
async def start_ad_creation(
    message: Message, state: FSMContext, user: User, session: AsyncSession
) -> None:
    """Start ad creation FSM after checking active ads limit."""
    ad_repo = AdRepository(session)
    active_count = await ad_repo.count_active_seller_ads(user.id)
    max_limit = get_max_active_ads()

    if active_count >= max_limit:
        await message.answer(
            text=(
                f"⚠️ **Превышен лимит объявлений!**\n\n"
                f"У вас уже активно **{active_count} из {max_limit}** бесплатных лотов.\n"
                "Чтобы создать новое объявление, удалите неактуальные лоты в разделе 📦 **Мои объявления**."
            ),
            reply_markup=get_main_menu_keyboard(),
            parse_mode="Markdown",
        )
        return

    await state.clear()
    await state.set_state(AdCreationStates.waiting_for_photo)
    await message.answer(
        text=(
            "📸 **Шаг 1 из 6: Скриншот оружия**\n\n"
            "Отправьте **один скриншот** оружия из игры Standoff 2, на котором четко видны скин и наклейки.\n\n"
            "Для отмены отправьте /cancel или нажмите кнопку «❌ Отмена» ниже 👇"
        ),
        reply_markup=get_cancel_fsm_keyboard(),
        parse_mode="Markdown",
    )


@router.callback_query(F.data == CB_CANCEL_FSM)
async def cancel_creation_callback(callback: CallbackQuery, state: FSMContext) -> None:
    """Cancel FSM creation via inline button."""
    await state.clear()
    await safe_edit_message(
        callback,
        "❌ **Создание объявления отменено.** Вы вернулись в главное меню.",
        parse_mode="Markdown",
    )
    await callback.answer("Отменено")


@router.message(AdCreationStates.waiting_for_photo, F.photo)
async def process_photo(message: Message, state: FSMContext) -> None:
    """Save photo file ID and prompt category selection."""
    if not message.photo:
        return
    photo_file_id = message.photo[-1].file_id
    await state.update_data(photo_file_id=photo_file_id)
    await state.set_state(AdCreationStates.waiting_for_category)

    await message.answer(
        text="📂 **Шаг 2 из 6: Выберите категорию оружия**",
        reply_markup=get_categories_keyboard(),
        parse_mode="Markdown",
    )


@router.message(AdCreationStates.waiting_for_photo, ~F.photo, ~F.text.startswith("/"))
async def process_photo_invalid(message: Message) -> None:
    """Reject non-photo inputs during photo upload step."""
    await message.answer(
        "⚠️ Пожалуйста, отправьте именно <b>скриншот (фотографию)</b> оружия, а не файл или текст.",
        parse_mode="HTML",
    )


@router.callback_query(
    AdCreationStates.waiting_for_category, F.data.startswith(CB_CATEGORY)
)
async def process_category(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle category selection and display weapons."""
    raw_category = callback.data[len(CB_CATEGORY) :]  # type: ignore[index]
    if raw_category.startswith("i:"):
        try:
            idx = int(raw_category[2:])
            category = get_categories()[idx]
        except (IndexError, ValueError):
            await callback.answer("Некорректная категория", show_alert=True)
            return
    else:
        category = raw_category

    if category not in get_categories():
        await callback.answer("Некорректная категория", show_alert=True)
        return

    await state.update_data(category=category)
    await state.set_state(AdCreationStates.waiting_for_weapon)
    await safe_edit_message(
        callback,
        text=f"📂 Категория: **{category}**\n\n🔫 **Шаг 3 из 6: Выберите модель оружия**",
        reply_markup=get_weapons_keyboard(category),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(AdCreationStates.waiting_for_weapon, F.data == CB_BACK_CATEGORY)
async def back_to_categories(callback: CallbackQuery, state: FSMContext) -> None:
    """Go back to category selection."""
    await state.set_state(AdCreationStates.waiting_for_category)
    await safe_edit_message(
        callback,
        text="📂 **Шаг 2 из 6: Выберите категорию оружия**",
        reply_markup=get_categories_keyboard(),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(
    AdCreationStates.waiting_for_weapon, F.data.startswith(CB_CATEGORY)
)
async def handle_category_in_weapon(callback: CallbackQuery, state: FSMContext) -> None:
    """Allow re-selecting category if clicked from weapon step."""
    await process_category(callback, state)


@router.callback_query(
    AdCreationStates.waiting_for_weapon, F.data.startswith(CB_WEAPON)
)
async def process_weapon(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle weapon model selection and display skins."""
    data = await state.get_data()
    category = data.get("category", "")
    raw_weapon = callback.data[len(CB_WEAPON) :]  # type: ignore[index]

    if raw_weapon.startswith("i:"):
        try:
            idx = int(raw_weapon[2:])
            weapon = get_weapons(category)[idx]
        except (IndexError, ValueError):
            await callback.answer("Некорректная модель", show_alert=True)
            return
    else:
        weapon = raw_weapon

    if weapon not in get_weapons(category):
        await callback.answer("Некорректная модель", show_alert=True)
        return

    await state.update_data(weapon=weapon)
    success = await safe_edit_message(
        callback,
        text=f"🔫 Оружие: **{weapon}**\n\n🎨 **Шаг 4 из 6: Выберите скин**",
        reply_markup=get_skins_keyboard(category, weapon),
        parse_mode="Markdown",
    )
    if success:
        await state.set_state(AdCreationStates.waiting_for_skin)
    await callback.answer()


@router.callback_query(AdCreationStates.waiting_for_skin, F.data == CB_BACK_WEAPON)
async def back_to_weapons(callback: CallbackQuery, state: FSMContext) -> None:
    """Go back to weapon selection."""
    data = await state.get_data()
    category = data.get("category", "")
    await state.set_state(AdCreationStates.waiting_for_weapon)
    await safe_edit_message(
        callback,
        text=f"📂 Категория: **{category}**\n\n🔫 **Шаг 3 из 6: Выберите модель оружия**",
        reply_markup=get_weapons_keyboard(category),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(
    AdCreationStates.waiting_for_skin, F.data.startswith(CB_WEAPON)
)
async def handle_weapon_in_skin(callback: CallbackQuery, state: FSMContext) -> None:
    """Allow re-selecting weapon if clicked while in skin step."""
    await process_weapon(callback, state)


@router.callback_query(AdCreationStates.waiting_for_skin, F.data.startswith(CB_SKIN))
async def process_skin(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle skin selection and prompt for sticker count."""
    data = await state.get_data()
    category = data.get("category", "")
    weapon = data.get("weapon", "")
    raw_skin = callback.data[len(CB_SKIN) :]  # type: ignore[index]

    if raw_skin.startswith("i:"):
        try:
            idx = int(raw_skin[2:])
            skin = get_skins(category, weapon)[idx]
        except (IndexError, ValueError):
            await callback.answer("Некорректный скин", show_alert=True)
            return
    else:
        skin = raw_skin

    if not is_valid_selection(category, weapon, skin):
        await callback.answer("Некорректный скин", show_alert=True)
        return

    await state.update_data(skin=skin)
    await state.set_state(AdCreationStates.waiting_for_sticker_count)
    await safe_edit_message(
        callback,
        text=f"🎨 Скин: **{weapon} | {skin}**\n\n🏷 **Шаг 5 из 6: Сколько наклеек нанесено на скин?**",
        reply_markup=get_sticker_counts_keyboard(),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(
    AdCreationStates.waiting_for_sticker_count, F.data.startswith(CB_SKIN)
)
async def handle_skin_in_stickers(callback: CallbackQuery, state: FSMContext) -> None:
    """Allow re-selecting skin if clicked while in sticker count step."""
    await process_skin(callback, state)


@router.callback_query(
    AdCreationStates.waiting_for_sticker_count, F.data == CB_BACK_SKIN
)
async def back_to_skins(callback: CallbackQuery, state: FSMContext) -> None:
    """Go back to skin selection."""
    data = await state.get_data()
    category = data.get("category", "")
    weapon = data.get("weapon", "")
    await state.set_state(AdCreationStates.waiting_for_skin)
    await safe_edit_message(
        callback,
        text=f"🔫 Оружие: **{weapon}**\n\n🎨 **Шаг 4 из 6: Выберите скин**",
        reply_markup=get_skins_keyboard(category, weapon),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(
    AdCreationStates.waiting_for_sticker_count, F.data.startswith(CB_STICKER_COUNT)
)
async def process_sticker_count(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle sticker count selection and proceed directly to price."""
    try:
        count = int(callback.data[len(CB_STICKER_COUNT) :])  # type: ignore[index]
    except ValueError:
        await callback.answer("Ошибка формата", show_alert=True)
        return

    if count < 0 or count > 4:
        await callback.answer("Допустимо от 0 до 4 наклеек", show_alert=True)
        return

    await state.update_data(sticker_count=count, sticker_rarity="—")
    await state.set_state(AdCreationStates.waiting_for_price)
    count_label = "<b>Без наклеек</b>" if count == 0 else f"<b>{count} шт.</b>"
    await safe_edit_message(
        callback,
        text=(
            f"🏷 Наклеек: {count_label}\n\n"
            "💰 <b>Шаг 6 из 6: Укажите цену в голде (G)</b>\n\n"
            "Отправьте цену целым положительным числом (например: <code>250</code>):"
        ),
        reply_markup=get_price_input_keyboard(),
    )
    await callback.answer()


@router.callback_query(
    AdCreationStates.waiting_for_price,
    F.data.in_({CB_BACK_RARITY, CB_BACK_COUNT}),
)
async def back_from_price_input(callback: CallbackQuery, state: FSMContext) -> None:
    """Go back from price step to sticker count selection."""
    data = await state.get_data()
    weapon = html.escape(data.get("weapon", ""))
    skin = html.escape(data.get("skin", ""))
    await state.set_state(AdCreationStates.waiting_for_sticker_count)
    await safe_edit_message(
        callback,
        text=f"🎨 Скин: <b>{weapon} | {skin}</b>\n\n🏷 <b>Шаг 5 из 6: Сколько наклеек нанесено на скин?</b>",
        reply_markup=get_sticker_counts_keyboard(),
    )
    await callback.answer()


@router.message(AdCreationStates.waiting_for_price, F.text, ~F.text.startswith("/"))
async def process_price(
    message: Message, state: FSMContext, user: User, session: AsyncSession
) -> None:
    """Validate price, persist ad, and present Telegram Stars promotion options."""
    if not message.text or not message.text.isdigit():
        await message.answer(
            "⚠️ Цена должна быть целым положительным числом (например `150`). Попробуйте еще раз:"
        )
        return

    price = int(message.text)
    if price <= 0 or price > 10_000_000:
        await message.answer(
            "⚠️ Введите реальную цену в диапазоне от 1 до 10 000 000 голды:"
        )
        return

    data = await state.get_data()
    editing_ad_id: int | None = data.get("editing_ad_id")
    if editing_ad_id:
        ad_repo = AdRepository(session)
        updated_ad = await ad_repo.update_ad_price(
            ad_id=editing_ad_id, seller_id=user.id, new_price=price
        )
        if updated_ad is None:
            await state.clear()
            await message.answer(
                "⚠️ Объявление не найдено или недоступно.",
                reply_markup=get_main_menu_keyboard(),
            )
            return

        updated_ad.seller = user
        await state.clear()

        await message.answer(
            f"✅ <b>Цена лота #{updated_ad.id} успешно обновлена:</b> <b>{price} G</b>!",
            parse_mode="HTML",
        )
        caption = format_ad_caption(updated_ad, mode="created")
        await message.answer_photo(
            photo=updated_ad.photo_file_id,
            caption=caption,
            reply_markup=get_promotion_keyboard(
                updated_ad.id,
                get_stars_turbo_price(),
                get_stars_standard_price(),
                is_admin=(user.id in settings.ADMIN_IDS),
            ),
            parse_mode="HTML",
        )
        return

    photo_file_id: str | None = data.get("photo_file_id")
    category: str | None = data.get("category")
    weapon: str | None = data.get("weapon")
    skin: str | None = data.get("skin")
    sticker_count: int | None = data.get("sticker_count")
    sticker_rarity: str = data.get("sticker_rarity") or "—"

    if (
        not photo_file_id
        or not category
        or not weapon
        or not skin
        or sticker_count is None
    ):
        await state.clear()
        await message.answer(
            "⚠️ Ошибка сессии создания лота. Пожалуйста, начните заново.",
            reply_markup=get_main_menu_keyboard(),
        )
        return

    ad_repo = AdRepository(session)

    ad = await ad_repo.create_ad_guarded(
        seller_id=user.id,
        category=category,
        weapon_model=weapon,
        skin_name=skin,
        sticker_count=sticker_count,
        sticker_rarity=sticker_rarity,
        price_gold=price,
        photo_file_id=photo_file_id,
        max_limit=get_max_active_ads(),
    )
    if ad is None:
        max_limit = get_max_active_ads()
        await state.clear()
        await message.answer(
            text=f"⚠️ Не удалось опубликовать: достигнут лимит {max_limit} активных лотов. Удалите ненужные в 📦 Мои объявления.",
            reply_markup=get_main_menu_keyboard(),
        )
        return

    ad.seller = user
    await state.clear()

    if message.bot:
        subscribers: list[int] = []
        try:
            sub_repo = SubscriptionRepository(session)
            subscribers = await sub_repo.find_matching_subscribers(ad)
            if subscribers:
                notif_task = asyncio.create_task(
                    notify_subscribers_about_new_ad(
                        bot=message.bot,
                        session=None,
                        ad=ad,
                        matching_user_ids=subscribers,
                    )
                )
                _background_tasks.add(notif_task)
                notif_task.add_done_callback(_background_tasks.discard)
        except Exception as exc:
            logger.error("Error scheduling subscriber notifications for ad #%d: %s", ad.id, exc)

        try:
            seller_name = user.first_name or (message.from_user.first_name if message.from_user else None)
            await autopost_new_ad(
                bot=message.bot,
                ad=ad,
                skip_user_ids=set(subscribers),
                seller_name=seller_name,
            )
        except Exception as exc:
            logger.error("Error triggering channel autopost for ad #%d: %s", ad.id, exc)

    caption = format_ad_caption(ad, mode="created")

    await message.answer_photo(
        photo=ad.photo_file_id,
        caption=caption,
        reply_markup=get_promotion_keyboard(
            ad.id,
            get_stars_turbo_price(),
            get_stars_standard_price(),
            is_admin=(user.id in settings.ADMIN_IDS),
            free_boosts=user.free_top_boosts,
            referrals_threshold=get_referrals_for_top(),
        ),
        parse_mode="HTML",
    )




@router.message(
    StateFilter(
        AdCreationStates.waiting_for_category,
        AdCreationStates.waiting_for_weapon,
        AdCreationStates.waiting_for_skin,
        AdCreationStates.waiting_for_sticker_count,
        AdCreationStates.waiting_for_sticker_rarity,
    ),
    ~F.text.startswith("/"),
    ~F.successful_payment,
)
async def process_ad_creation_button_fallback(message: Message) -> None:
    """Handle text/media messages when user is expected to click inline buttons."""
    await message.answer(
        "👆 Пожалуйста, используйте кнопки под сообщением для выбора параметра лота, или нажмите «❌ Отмена»."
    )


@router.message(AdCreationStates.waiting_for_price, ~F.text, ~F.successful_payment)
async def process_price_non_text_fallback(message: Message) -> None:
    """Handle non-text media input during price entry step."""
    await message.answer(
        "⚠️ Пожалуйста, отправьте цену в виде целого числа (голда) текстовым сообщением:"
    )




@router.callback_query(F.data.startswith(f"{CB_PROMO}skip:"))
async def promo_skip(callback: CallbackQuery) -> None:
    """Acknowledge free publication without Stars promotion."""
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            text="👍 Объявление опубликовано на стандартных условиях в общей ленте.",
            reply_markup=get_main_menu_keyboard(),
        )
    await callback.answer("Опубликовано бесплатно")


@router.callback_query(
    F.data.startswith(f"{CB_PROMO}turbo:") | F.data.startswith(f"{CB_PROMO}standard:")
)
async def promo_invoice(
    callback: CallbackQuery, user: User, session: AsyncSession
) -> None:
    """Send Telegram Stars payment invoice for Turbo or Standard promotion."""
    if not callback.data:
        await callback.answer()
        return
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Неверные данные запроса.", show_alert=True)
        return
    tariff = parts[1]
    try:
        ad_id = int(parts[2])
    except ValueError:
        await callback.answer("Некорректный ID объявления.", show_alert=True)
        return

    ad_repo = AdRepository(session)
    ad = await ad_repo.get_by_id(ad_id)
    if ad is None or ad.seller_id != user.id or not ad.is_active:
        await callback.answer(
            "Объявление не найдено или не принадлежит вам.", show_alert=True
        )
        return

    is_turbo = tariff == "turbo"
    stars_amount = (
        get_stars_turbo_price() if is_turbo else get_stars_standard_price()
    )
    title = f"Продвижение «{'Турбо' if is_turbo else 'Стандарт'}»"
    desc = (
        f"Закрепление лота {ad.weapon_model} | {ad.skin_name} в ТОПе выдачи на "
        f"{'3 дня + значок 🔥' if is_turbo else '24 часа'}."
    )
    payload = f"stars_promo:{tariff}:{ad.id}:{user.id}"

    prices = [LabeledPrice(label="Оплата звездами", amount=stars_amount)]

    if callback.bot and isinstance(callback.message, Message):
        await callback.bot.send_invoice(
            chat_id=callback.message.chat.id,
            title=title,
            description=desc,
            payload=payload,
            currency="XTR",
            prices=prices,
            provider_token="",  # nosec B106: Telegram Stars invoices require empty provider_token
        )
    await callback.answer()


@router.pre_checkout_query()
async def process_pre_checkout(
    pre_checkout_query: PreCheckoutQuery, session: AsyncSession
) -> None:
    """Validate invoice payload and seller ownership prior to payment completion."""
    payload = pre_checkout_query.invoice_payload
    parts = payload.split(":")

    if len(parts) != 4 or parts[0] != "stars_promo":
        await pre_checkout_query.answer(
            ok=False, error_message="Некорректные данные платежа."
        )
        return

    _, tariff, ad_id_str, user_id_str = parts
    if tariff not in ("turbo", "standard"):
        await pre_checkout_query.answer(ok=False, error_message="Недопустимый тариф.")
        return

    try:
        ad_id = int(ad_id_str)
        user_id = int(user_id_str)
    except ValueError:
        await pre_checkout_query.answer(
            ok=False, error_message="Ошибка параметров платежа."
        )
        return

    expected_amount = (
        get_stars_turbo_price()
        if tariff == "turbo"
        else get_stars_standard_price()
    )
    if pre_checkout_query.total_amount != expected_amount:
        logger.warning(
            "Pre-checkout amount mismatch for user %d: received %d, expected %d",
            user_id,
            pre_checkout_query.total_amount,
            expected_amount,
        )
        await pre_checkout_query.answer(
            ok=False,
            error_message="Несоответствие суммы платежа установленному тарифу.",
        )
        return

    if pre_checkout_query.from_user.id != user_id:
        await pre_checkout_query.answer(
            ok=False, error_message="Несоответствие плательщика."
        )
        return

    ad_repo = AdRepository(session)
    ad = await ad_repo.get_by_id(ad_id)
    if ad is None or ad.seller_id != user_id or not ad.is_active:
        await pre_checkout_query.answer(
            ok=False, error_message="Объявление снято с продажи или удалено."
        )
        return

    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def process_successful_payment(message: Message, session: AsyncSession) -> None:
    """Grant TOP status and log transaction upon successful Stars payment."""
    if not message.successful_payment:
        return

    payload = message.successful_payment.invoice_payload
    parts = payload.split(":")
    if len(parts) != 4 or parts[0] != "stars_promo":
        return

    _, tariff, ad_id_str, user_id_str = parts
    try:
        ad_id = int(ad_id_str)
        user_id = int(user_id_str)
    except ValueError:
        logger.error("Invalid payment payload integer conversion: %s", payload)
        return
    charge_id = message.successful_payment.telegram_payment_charge_id
    stars_amount = message.successful_payment.total_amount

    expected_amount = (
        get_stars_turbo_price()
        if tariff == "turbo"
        else get_stars_standard_price()
    )
    if stars_amount != expected_amount:
        logger.error(
            "Successful payment amount mismatch for user %d, ad %d: received %d, expected %d",
            user_id,
            ad_id,
            stars_amount,
            expected_amount,
        )
        await message.answer(
            text="⚠️ Ошибка валидации суммы платежа. Обратитесь в поддержку.",
            reply_markup=get_main_menu_keyboard(),
            parse_mode="Markdown",
        )
        return

    ad_repo = AdRepository(session)
    duration_days = 3 if tariff == "turbo" else 1

    ad = await ad_repo.get_by_id(ad_id)
    if ad is None or not ad.is_active or ad.seller_id != user_id:
        logger.warning(
            "Ad #%d inactive or invalid for Star payment by user %d. Issuing refund for charge %s.",
            ad_id,
            user_id,
            charge_id,
        )
        if ad is not None:
            await ad_repo.record_star_transaction(
                user_id=user_id,
                ad_id=ad_id,
                tariff=f"{tariff}_refund",
                stars_amount=stars_amount,
                telegram_payment_charge_id=charge_id,
            )
        try:
            await message.bot.refund_star_payment(
                user_id=user_id,
                telegram_payment_charge_id=charge_id,
            )
            await message.answer(
                text="⚠️ Объявление уже неактивно или удалено. Оплата звёздами автоматически возвращена на ваш баланс Telegram.",
                reply_markup=get_main_menu_keyboard(),
            )
        except TelegramAPIError as exc:
            logger.error("Failed to refund star payment %s for user %d: %s", charge_id, user_id, exc)
            await message.answer(
                text="⚠️ Объявление неактивно. Произошла ошибка автоматического возврата — пожалуйста, обратитесь в поддержку.",
                reply_markup=get_main_menu_keyboard(),
            )
        return

    success = await ad_repo.activate_top_status(ad_id=ad_id, duration_days=duration_days)
    await ad_repo.record_star_transaction(
        user_id=user_id,
        ad_id=ad_id,
        tariff=tariff if success else f"{tariff}_failed",
        stars_amount=stars_amount,
        telegram_payment_charge_id=charge_id,
    )

    logger.info("Activated %s TOP status for ad #%d by user %d", tariff, ad_id, user_id)
    await message.answer(
        text=(
            "🎉 <b>Оплата успешно принята!</b>\n\n"
            f"Ваш лот #{ad_id} закреплен в ТОПе выдачи на <b>{duration_days} дн.</b> "
            f"{'со значком 🔥' if tariff == 'turbo' else ''}."
        ),
        reply_markup=get_main_menu_keyboard(),
    )




@router.message(F.text == BTN_MY_ADS)
@router.message(Command("my_ads"))
async def handle_my_ads(
    message: Message, user: User, session: AsyncSession, state: FSMContext
) -> None:
    """Display seller's personal cabinet with active lots, views, and clicks."""
    await state.clear()
    ad_repo = AdRepository(session)
    ads = await ad_repo.get_seller_ads(user.id)

    if not ads:
        await message.answer(
            text=(
                "📦 **Личный кабинет продавца**\n\n"
                "У вас сейчас нет активных объявлений.\n\n"
                "Чтобы выставить скин на продажу, нажмите кнопку 💰 **Продать скин**."
            ),
            reply_markup=get_main_menu_keyboard(),
            parse_mode="Markdown",
        )
        return

    max_limit = get_max_active_ads()
    await message.answer(
        text=f"📦 **Ваши объявления ({len(ads)} из {max_limit}):**",
        parse_mode="Markdown",
    )

    for ad in ads:
        caption = format_ad_caption(ad, mode="seller")

        await safe_answer_photo(
            message=message,
            photo=ad.photo_file_id,
            caption=caption,
            reply_markup=get_seller_ad_keyboard(
                ad.id, ad.is_top, free_boosts=user.free_top_boosts
            ),
            parse_mode="HTML",
            ad_id=ad.id,
        )


@router.callback_query(F.data.startswith(CB_DELETE_AD))
async def confirm_ad_delete(callback: CallbackQuery) -> None:
    """Prompt confirmation before deleting an ad."""
    if not callback.data:
        await callback.answer()
        return
    try:
        ad_id = int(callback.data[len(CB_DELETE_AD) :])
    except (IndexError, ValueError):
        await callback.answer("Ошибка запроса", show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(
            reply_markup=get_delete_confirmation_keyboard(ad_id)
        )
    await callback.answer()


@router.callback_query(F.data.startswith("cancel_del:") | (F.data == "cancel_delete"))
async def cancel_delete(
    callback: CallbackQuery, user: User, session: AsyncSession
) -> None:
    """Cancel deletion and restore normal ad buttons."""
    ad_id = 0
    if callback.data and ":" in callback.data:
        try:
            ad_id = int(callback.data.split(":")[1])
        except (IndexError, ValueError):
            ad_id = 0

    ad_repo = AdRepository(session)
    ad = await ad_repo.get_by_id(ad_id) if ad_id else None
    is_top = ad.is_top if ad else False

    await callback.answer("Удаление отменено")
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(
            reply_markup=get_seller_ad_keyboard(
                ad_id, is_top, free_boosts=user.free_top_boosts
            )
            if ad_id
            else None
        )


@router.callback_query(F.data.startswith(CB_CONFIRM_DELETE))
async def process_ad_delete(
    callback: CallbackQuery, user: User, session: AsyncSession
) -> None:
    """Soft-delete listing (IDOR-safe)."""
    if not callback.data:
        await callback.answer()
        return
    try:
        ad_id = int(callback.data[len(CB_CONFIRM_DELETE) :])
    except (IndexError, ValueError):
        await callback.answer("Ошибка запроса", show_alert=True)
        return
    ad_repo = AdRepository(session)
    deleted = await ad_repo.delete_ad(ad_id=ad_id, seller_id=user.id)

    if deleted:
        await safe_edit_message(
            callback,
            text="🗑 **Объявление успешно снято с продажи.**",
            parse_mode="Markdown",
        )
        await callback.answer("Объявление удалено")
    else:
        await callback.answer(
            "Ошибка: объявление уже удалено или не принадлежит вам.", show_alert=True
        )


@router.callback_query(F.data.startswith(CB_BOOST_AD))
async def process_boost_ad(
    callback: CallbackQuery, user: User, session: AsyncSession
) -> None:
    """Open promotion menu for an already existing active lot."""
    if not callback.data:
        await callback.answer()
        return
    try:
        ad_id = int(callback.data[len(CB_BOOST_AD) :])
    except (IndexError, ValueError):
        await callback.answer("Ошибка запроса", show_alert=True)
        return
    ad_repo = AdRepository(session)
    ad = await ad_repo.get_by_id(ad_id)

    if ad is None or ad.seller_id != user.id or not ad.is_active:
        await callback.answer("Лот недоступен.", show_alert=True)
        return

    if isinstance(callback.message, Message):
        await callback.message.reply(
            text=f"🚀 <b>Выберите тариф закрепления для лота #{ad_id}:</b>",
            reply_markup=get_promotion_keyboard(
                ad.id,
                get_stars_turbo_price(),
                get_stars_standard_price(),
                is_boost=True,
                is_admin=(user.id in settings.ADMIN_IDS),
                free_boosts=user.free_top_boosts,
                referrals_threshold=get_referrals_for_top(),
            ),
            parse_mode="HTML",
        )
    await callback.answer()


@router.callback_query(F.data.startswith(f"{CB_PROMO}test:"))
async def promo_test_admin(
    callback: CallbackQuery, user: User, session: AsyncSession
) -> None:
    """Test activation of TOP status for admins without Stars payment."""
    if user.id not in settings.ADMIN_IDS:
        await callback.answer("Доступно только администраторам.", show_alert=True)
        return
    if not callback.data:
        await callback.answer()
        return
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Неверные данные запроса.", show_alert=True)
        return
    try:
        ad_id = int(parts[2])
    except ValueError:
        await callback.answer("Некорректный ID объявления.", show_alert=True)
        return
    ad_repo = AdRepository(session)
    ad = await ad_repo.get_by_id(ad_id)
    if ad is None or ad.seller_id != user.id or not ad.is_active:
        await callback.answer("Объявление не найдено или не принадлежит вам.", show_alert=True)
        return

    await ad_repo.activate_top_status(ad_id=ad_id, duration_days=3)
    await ad_repo.record_star_transaction(
        user_id=user.id,
        ad_id=ad_id,
        tariff="test_admin",
        stars_amount=0,
        telegram_payment_charge_id=f"TEST_ADMIN_{user.id}_{ad_id}",
    )
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            text=(
                f"🧪 **[ТЕСТОВЫЙ РЕЖИМ АДМИНИСТРАТОРА]**\n\n"
                f"✅ Объявление #{ad.id} успешно закреплено в **ТОП (3 дня + 🔥)** без списания звёзд!\n"
                f"Проверьте его статус в каталоге или в разделе 📦 **Мои объявления**."
            ),
            reply_markup=get_main_menu_keyboard(),
            parse_mode="Markdown",
        )
    await callback.answer("Тестовый ТОП активирован!")


@router.callback_query(F.data.startswith(f"{CB_PROMO}edit_price:"))
async def promo_start_edit_price(
    callback: CallbackQuery, user: User, state: FSMContext, session: AsyncSession
) -> None:
    """Prompt seller to modify price for an ad just created."""
    if not callback.data:
        await callback.answer()
        return
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Неверные данные запроса.", show_alert=True)
        return
    try:
        ad_id = int(parts[2])
    except ValueError:
        await callback.answer("Некорректный ID объявления.", show_alert=True)
        return
    ad_repo = AdRepository(session)
    ad = await ad_repo.get_by_id(ad_id)
    if ad is None or ad.seller_id != user.id or not ad.is_active:
        await callback.answer("Объявление не найдено или недоступно.", show_alert=True)
        return

    await state.set_state(AdCreationStates.waiting_for_price)
    await state.update_data(editing_ad_id=ad.id)

    if isinstance(callback.message, Message):
        await callback.message.reply(
            text=(
                f"✏️ **Редактирование цены лота #{ad.id}** ({ad.weapon_model} | {ad.skin_name})\n\n"
                f"Текущая цена: **{ad.price_gold} G**.\n"
                "Введите новую стоимость оружия в игре целым числом (от 1 до 10 000 000) "
                "или выберите действие ниже 👇"
            ),
            reply_markup=get_edit_price_keyboard(ad.id),
            parse_mode="Markdown",
        )
    await callback.answer()


@router.callback_query(F.data.startswith(f"{CB_PROMO}cancel_edit:"))
async def promo_cancel_edit(
    callback: CallbackQuery, state: FSMContext
) -> None:
    """Cancel price editing step."""
    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.delete()
    await callback.answer("Редактирование цены отменено")


@router.callback_query(F.data.startswith(f"{CB_PROMO}recreate:"))
async def promo_recreate_ad(
    callback: CallbackQuery, user: User, state: FSMContext, session: AsyncSession
) -> None:
    """Delete just created ad and restart wizard from step 1."""
    if not callback.data:
        await callback.answer()
        return
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Неверные данные запроса.", show_alert=True)
        return
    try:
        ad_id = int(parts[2])
    except ValueError:
        await callback.answer("Некорректный ID объявления.", show_alert=True)
        return
    ad_repo = AdRepository(session)
    await ad_repo.delete_ad(ad_id=ad_id, seller_id=user.id)
    await state.clear()
    await state.set_state(AdCreationStates.waiting_for_photo)

    if isinstance(callback.message, Message):
        await callback.message.answer(
            text=(
                f"🗑 Предыдущее объявление #{ad_id} удалено.\n\n"
                "📸 **Шаг 1 из 7: Скриншот оружия**\n\n"
                "Отправьте **один скриншот** оружия из игры Standoff 2, на котором четко видны скин и наклейки.\n\n"
                "Для отмены отправьте /cancel или нажмите кнопку «❌ Отмена» ниже 👇"
            ),
            reply_markup=get_cancel_fsm_keyboard(),
            parse_mode="Markdown",
        )
    await callback.answer("Создание заново")


@router.callback_query(F.data.startswith(f"{CB_PROMO}cancel:"))
async def promo_cancel(callback: CallbackQuery) -> None:
    """Cancel promotion menu popup for an existing lot."""
    if isinstance(callback.message, Message):
        await callback.message.delete()
    await callback.answer("Закрепление отменено")


@router.callback_query(F.data.startswith(f"{CB_PROMO}ref_info:"))
async def promo_referral_info(
    callback: CallbackQuery, user: User, session: AsyncSession
) -> None:
    """Display referral invite link, progress, and share button from promo screen."""
    if not callback.data:
        await callback.answer()
        return
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Неверные данные запроса.", show_alert=True)
        return
    try:
        ad_id = int(parts[2])
    except ValueError:
        await callback.answer("Некорректный ID объявления.", show_alert=True)
        return

    bot_user = await callback.bot.get_me()
    bot_username = bot_user.username or "Standsticker_bot"
    invite_url = f"https://t.me/{bot_username}?start=ref_{user.id}"

    ref_repo = ReferralRepository(session)
    ref_count = await ref_repo.get_referrals_count(user.id)
    threshold = get_referrals_for_top()
    remainder = ref_count % threshold
    needed = threshold - remainder

    share_text = (
        "🔥 Покупай и продавай редкие скины с наклейками в Standoff 2 "
        "без комиссии в проверенном маркетплейсе Standsticker!"
    )

    text = (
        "👥 <b>Бесплатный ТОП за приглашение друзей</b>\n\n"
        f"Приглашайте друзей и получайте бесплатное закрепление ваших объявлений "
        f"в ТОПе ленты на 24 часа за каждых <b>{threshold}</b> приглашенных!\n\n"
        f"📊 <b>Ваш прогресс:</b>\n"
        f"• Всего приглашено: <b>{ref_count}</b>\n"
        f"• До следующего бонуса осталось: <b>{needed}</b>\n"
        f"• Доступно бесплатных ТОПов: <b>{user.free_top_boosts}</b>\n\n"
        f"🔗 <b>Ваша реферальная ссылка:</b>\n"
        f"<code>{invite_url}</code>\n\n"
        "💡 <i>Нажмите кнопку ниже, чтобы переслать ссылку друзьям или опубликовать в канале!</i>"
    )

    back_cb = f"{CB_PROMO}back_to_promo:{ad_id}"
    reply_markup = get_referral_share_keyboard(
        invite_url=invite_url,
        share_text=share_text,
        back_callback=back_cb,
    )

    if isinstance(callback.message, Message):
        if callback.message.caption:
            await callback.message.edit_caption(
                caption=text,
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
        else:
            await callback.message.edit_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
    await callback.answer()


@router.callback_query(F.data.startswith(f"{CB_PROMO}back_to_promo:"))
async def promo_back_to_tariffs(
    callback: CallbackQuery, user: User, session: AsyncSession
) -> None:
    """Return from referral info back to promo tariffs."""
    if not callback.data:
        await callback.answer()
        return
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer()
        return
    try:
        ad_id = int(parts[2])
    except ValueError:
        await callback.answer()
        return

    ad_repo = AdRepository(session)
    ad = await ad_repo.get_by_id(ad_id)
    if not ad:
        await callback.answer("Объявление не найдено.", show_alert=True)
        return

    reply_markup = get_promotion_keyboard(
        ad.id,
        get_stars_turbo_price(),
        get_stars_standard_price(),
        is_boost=False,
        is_admin=(user.id in settings.ADMIN_IDS),
        free_boosts=user.free_top_boosts,
        referrals_threshold=get_referrals_for_top(),
    )

    if isinstance(callback.message, Message):
        if callback.message.caption:
            caption = format_ad_caption(ad, mode="created")
            await callback.message.edit_caption(
                caption=caption,
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
        else:
            text = f"🚀 <b>Выберите тариф закрепления для лота #{ad_id}:</b>"
            await callback.message.edit_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
    await callback.answer()


@router.callback_query(
    F.data.startswith(f"{CB_PROMO}free_boost:") | F.data.startswith(CB_FREE_BOOST)
)
async def promo_activate_free_boost(
    callback: CallbackQuery, user: User, session: AsyncSession
) -> None:
    """Activate 24h free TOP boost using earned referral reward."""
    if not callback.data:
        await callback.answer()
        return

    try:
        if callback.data.startswith(CB_FREE_BOOST):
            ad_id = int(callback.data[len(CB_FREE_BOOST) :])
        else:
            ad_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Некорректный ID объявления.", show_alert=True)
        return

    ad_repo = AdRepository(session)
    ad = await ad_repo.get_by_id(ad_id)
    if ad is None or ad.seller_id != user.id or not ad.is_active:
        await callback.answer(
            "Объявление не найдено или не принадлежит вам.", show_alert=True
        )
        return

    if ad.is_top:
        await callback.answer("Это объявление уже находится в ТОПе!", show_alert=True)
        return

    ref_repo = ReferralRepository(session)
    consumed = await ref_repo.consume_free_boost(user.id)
    if not consumed:
        await callback.answer(
            "У вас нет доступных бесплатных ТОПов. Пригласите друзей, чтобы получить их!",
            show_alert=True,
        )
        return

    await ad_repo.activate_top_status(ad_id=ad.id, duration_days=1)
    await ad_repo.record_star_transaction(
        user_id=user.id,
        ad_id=ad.id,
        tariff="free_referral_boost",
        stars_amount=0,
        telegram_payment_charge_id=f"FREE_REF_{user.id}_{ad.id}_{int(datetime.now(UTC).timestamp())}",
    )

    await session.refresh(user)
    ad.is_top = True

    logger.info("User %d activated free TOP boost for ad #%d", user.id, ad.id)

    if isinstance(callback.message, Message):
        new_caption = format_ad_caption(ad, mode="seller")
        new_keyboard = get_seller_ad_keyboard(
            ad.id, is_top=True, free_boosts=user.free_top_boosts
        )
        if callback.message.caption:
            await callback.message.edit_caption(
                caption=new_caption,
                reply_markup=new_keyboard,
                parse_mode="HTML",
            )
        else:
            await callback.message.edit_reply_markup(reply_markup=new_keyboard)

        await callback.message.answer(
            text=(
                f"🎉 <b>Бесплатный ТОП успешно активирован!</b>\n\n"
                f"Лот #{ad.id} ({ad.weapon_model} | {ad.skin_name}) закреплен "
                f"в ТОПе выдачи на <b>24 часа</b>.\n"
                f"Осталось бесплатных ТОПов: <b>{user.free_top_boosts}</b>"
            ),
            reply_markup=get_main_menu_keyboard(),
            parse_mode="HTML",
        )

    await callback.answer("🎉 ТОП на 24 часа активирован!", show_alert=True)
