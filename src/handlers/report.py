import html
import logging
import time

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.database.models import User
from src.database.repositories.ad_repo import AdRepository
from src.database.repositories.admin_repo import AdminRepository
from src.keyboards.admin import (
    CB_REPORT_CANCEL,
    CB_REPORT_REASON,
    get_report_action_keyboard,
    get_report_reasons_keyboard,
)
from src.keyboards.buyer import CB_REPORT_AD
from src.states.admin import ReportStates
from src.utils.helpers import safe_edit_message

logger = logging.getLogger(__name__)

router = Router(name="report")

REPORT_COOLDOWN_SECONDS = 180
_last_report_timestamps: dict[int, float] = {}
MAX_RATE_LIMIT_ENTRIES = 5000


def check_report_rate_limit(user_id: int) -> bool:
    """Return True if user is allowed to submit a report, False if on cooldown.

    Periodically prunes stale entries to prevent memory leaks.
    """
    now = time.time()
    if len(_last_report_timestamps) > MAX_RATE_LIMIT_ENTRIES:
        stale_keys = [
            uid
            for uid, ts in _last_report_timestamps.items()
            if now - ts >= REPORT_COOLDOWN_SECONDS
        ]
        for uid in stale_keys:
            _last_report_timestamps.pop(uid, None)

    last_time = _last_report_timestamps.get(user_id, 0.0)
    if now - last_time < REPORT_COOLDOWN_SECONDS:
        return False
    _last_report_timestamps[user_id] = now
    return True


@router.callback_query(F.data.startswith(CB_REPORT_AD))
async def prompt_report_ad(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    """Prompt user to select report reason when clicking report under an ad."""
    if not callback.data:
        return
    try:
        ad_id = int(callback.data[len(CB_REPORT_AD) :])
    except (IndexError, ValueError):
        await callback.answer("Ошибка параметров лота.", show_alert=True)
        return

    ad_repo = AdRepository(session)
    ad = await ad_repo.get_by_id(ad_id)
    if ad is None or not ad.is_active:
        await callback.answer("Объявление больше недоступно.", show_alert=True)
        return

    user_id = callback.from_user.id
    last_time = _last_report_timestamps.get(user_id, 0.0)
    remaining = int(REPORT_COOLDOWN_SECONDS - (time.time() - last_time))
    if remaining > 0:
        await callback.answer(
            f"⏳ Вы недавно отправляли жалобу. Подождите {remaining} сек. перед новой отправкой.",
            show_alert=True,
        )
        return

    await state.set_state(ReportStates.waiting_for_reason)
    await state.update_data(
        ad_id=ad.id,
        violator_id=ad.seller_id,
        chat_id=None,
    )

    if isinstance(callback.message, Message):
        ad_title = html.escape(f"{ad.weapon_model} | {ad.skin_name}")
        await callback.message.reply(
            text=(
                f"⚠️ <b>Пожаловаться на лот #{ad.id}</b> ({ad_title})\n\n"
                "Пожалуйста, выберите причину нарушения правил:"
            ),
            reply_markup=get_report_reasons_keyboard(),
        )
    await callback.answer()


@router.callback_query(ReportStates.waiting_for_reason, F.data == CB_REPORT_CANCEL)
async def cancel_report(callback: CallbackQuery, state: FSMContext) -> None:
    """Cancel report submission."""
    await state.clear()
    await safe_edit_message(callback, "❌ Подача жалобы отменена.")
    await callback.answer("Отменено")


@router.callback_query(
    ReportStates.waiting_for_reason, F.data.startswith(CB_REPORT_REASON)
)
async def process_report_reason(
    callback: CallbackQuery,
    state: FSMContext,
    user: User,
    session: AsyncSession,
) -> None:
    """Save complaint and notify admins with cooldown enforcement."""
    if not callback.data:
        return
    reason_key = callback.data[len(CB_REPORT_REASON) :]
    reason_map = {
        "scam": "Скам / Мошенничество",
        "illegal": "Запрещенный контент / Правила игры",
        "abuse": "Оскорбления / Спам",
        "other": "Другое нарушение",
    }
    reason_desc = reason_map.get(reason_key, "Нарушение правил")

    data = await state.get_data()
    ad_id: int | None = data.get("ad_id")
    violator_id: int | None = data.get("violator_id")
    chat_id: int | None = data.get("chat_id")
    await state.clear()

    if violator_id is None:
        await callback.answer("Ошибка параметров жалобы.", show_alert=True)
        return

    if not check_report_rate_limit(user.id):
        await callback.answer(
            "⏳ Слишком много жалоб. Пожалуйста, подождите перед следующей отправкой.",
            show_alert=True,
        )
        return

    admin_repo = AdminRepository(session)
    report = await admin_repo.create_report(
        reporter_id=user.id,
        violator_id=violator_id,
        ad_id=ad_id,
        chat_id=chat_id,
        reason=reason_desc,
    )

    await safe_edit_message(
        callback,
        text="✅ <b>Спасибо за бдительность!</b>\nЖалоба передана модераторам на рассмотрение.",
        reply_markup=None,
    )
    await callback.answer("Жалоба принята")

    if callback.bot:
        reporter_username = f"@{html.escape(user.username)}" if user.username else "—"
        alert_text = (
            f"🚨 <b>Новая жалоба #{report.id}</b>\n\n"
            f"👤 Заявитель: <code>{user.id}</code> ({reporter_username})\n"
            f"🚫 Нарушитель: <code>{violator_id}</code>\n"
            f"📦 Лот: <code>#{ad_id or '—'}</code> | 💬 Чат: <code>#{chat_id or '—'}</code>\n"
            f"📝 Причина: <b>{html.escape(reason_desc)}</b>"
        )
        keyboard = get_report_action_keyboard(
            report_id=report.id,
            violator_id=violator_id,
            chat_id=chat_id,
            ad_id=ad_id,
        )
        for admin_id in settings.ADMIN_IDS:
            try:
                await callback.bot.send_message(
                    chat_id=admin_id,
                    text=alert_text,
                    reply_markup=keyboard,
                )
            except TelegramAPIError as e:
                logger.warning("Could not alert admin %d: %s", admin_id, e)
