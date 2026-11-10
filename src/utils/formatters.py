"""Unified formatting utilities for marketplace listings and cards using valid HTML."""

from datetime import UTC, datetime
from html import escape
from typing import Literal

from src.database.models import Ad


def format_sticker_label(count: int, rarity: str | None, bold: bool = True) -> str:
    """Format sticker count and rarity label with optional bold HTML markup."""
    if count == 0:
        return "<b>Без наклеек</b>" if bold else "Без наклеек"
    rarity_str = f" ({escape(rarity)})" if rarity else ""
    label = f"{count} шт.{rarity_str}"
    return f"<b>{label}</b>" if bold else label


def format_ad_caption(
    ad: Ad,
    mode: Literal["buyer", "seller", "push", "admin", "created"] = "buyer",
) -> str:
    """Generate uniform HTML caption for skin listing cards across the app."""
    weapon_title = f"{escape(ad.weapon_model)} | {escape(ad.skin_name)}"

    if mode == "buyer":
        top_badge = "🔥 <b>[ТОП-объявление]</b>\n" if ad.is_top else ""
        seller = ad.seller
        if seller and seller.ratings_count > 0:
            rating_val = f"{seller.rating:.1f} ⭐"
            reviews_str = f"({seller.ratings_count} отзывов)"
        else:
            rating_val = "Новый продавец"
            reviews_str = "(без отзывов)"

        days_in_bot = (
            (datetime.now(UTC) - seller.registration_date).days
            if seller and seller.registration_date
            else 0
        )
        if seller:
            raw_name = seller.first_name or (
                f"@{seller.username}" if seller.username else f"ID {seller.id}"
            )
        else:
            raw_name = "Продавец"
        seller_name = escape(raw_name)

        sticker_label = format_sticker_label(
            ad.sticker_count, ad.sticker_rarity, bold=True
        )
        return (
            f"{top_badge}"
            f"🔫 <b>{weapon_title}</b>\n"
            f"🏷 Наклеек: {sticker_label}\n"
            f"💰 Цена: <b>{ad.price_gold} G</b>\n\n"
            f"👤 <b>Продавец:</b> {seller_name}\n"
            f"⭐ Рейтинг: <b>{rating_val}</b> {reviews_str}\n"
            f"⏳ В боте: <b>{days_in_bot} дн.</b>\n\n"
            f"👁 Просмотров: <b>{ad.views_count + 1}</b> | 📅 {ad.created_at.strftime('%d.%m.%Y')}"
        )

    if mode == "seller":
        top_badge = "🔥 <b>[ТОП-объявление]</b>\n" if ad.is_top else ""
        top_info = ""
        if ad.is_top and ad.top_until:
            expiry = ad.top_until.strftime("%d.%m.%Y %H:%M UTC")
            top_info = f"⏳ ТОП действует до: {expiry}\n"

        sticker_label = format_sticker_label(
            ad.sticker_count, ad.sticker_rarity, bold=True
        )
        return (
            f"{top_badge}"
            f"🔫 <b>{weapon_title}</b>\n"
            f"🏷 Наклеек: {sticker_label}\n"
            f"💰 Цена: <b>{ad.price_gold} G</b>\n"
            f"👁 Просмотров: <b>{ad.views_count}</b> | 💬 Запросов в чат: <b>{ad.clicks_count}</b>\n"
            f"{top_info}"
            f"📅 Опубликовано: {ad.created_at.strftime('%d.%m.%Y')}"
        )

    if mode == "push":
        top_badge = "🔥 <b>[ТОП]</b>\n" if ad.is_top else ""
        seller = ad.seller
        seller_rating = (
            f"{seller.rating:.1f} ⭐"
            if (seller and seller.ratings_count > 0)
            else "Новый продавец"
        )
        days_in_bot = (
            (datetime.now(UTC) - seller.registration_date).days
            if seller and seller.registration_date
            else 0
        )
        raw_name = (
            seller.first_name
            or (f"@{seller.username}" if seller.username else f"ID {seller.id}")
            if seller
            else "Продавец"
        )
        seller_name = escape(raw_name)
        sticker_label = format_sticker_label(
            ad.sticker_count, ad.sticker_rarity, bold=True
        )
        return (
            "🔔 <b>Найден скин по вашей подписке!</b>\n\n"
            f"{top_badge}"
            f"🔫 <b>{weapon_title}</b>\n"
            f"🏷 Наклеек: {sticker_label}\n"
            f"💰 Цена: <b>{ad.price_gold} G</b>\n\n"
            f"👤 Продавец: <b>{seller_name}</b> | {seller_rating} (в боте {days_in_bot} дн.)\n\n"
            "Свяжитесь с продавцом прямо сейчас, пока скин не купили!"
        )

    if mode == "admin":
        sticker_label = format_sticker_label(
            ad.sticker_count, ad.sticker_rarity, bold=False
        )
        status_str = "🟢 Активен" if ad.is_active else "🔴 Снят с продажи"
        return (
            f"📦 <b>Лот #{ad.id}</b> ({status_str})\n\n"
            f"🔫 <b>Предмет:</b> {weapon_title}\n"
            f"🏷 <b>Наклейки:</b> {sticker_label}\n"
            f"💰 <b>Цена:</b> {ad.price_gold} G\n"
            f"👤 <b>Продавец ID:</b> <code>{ad.seller_id}</code>\n"
            f"👁 Просмотров: {ad.views_count} | 💬 Переходов в чат: {ad.clicks_count}\n"
            f"📅 Создан: {ad.created_at.strftime('%d.%m.%Y %H:%M')}"
        )

    if mode == "created":
        sticker_label = format_sticker_label(
            ad.sticker_count, ad.sticker_rarity, bold=True
        )
        return (
            "✅ <b>Объявление успешно создано!</b>\n\n"
            f"🔫 <b>{weapon_title}</b>\n"
            f"🏷 Наклеек: {sticker_label}\n"
            f"💰 Цена: <b>{ad.price_gold} G</b>\n\n"
            "🚀 <b>Хотите ускорить продажу?</b>\n"
            "Закрепите объявление в ТОПе выдачи с помощью Telegram Stars ⭐️:"
        )

    raise ValueError(f"Unknown format mode: {mode}")
