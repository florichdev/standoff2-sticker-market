import asyncio
import logging
import ssl
from typing import Any

import aiohttp
from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardMarkup, Message

from src.config import settings

logger = logging.getLogger(__name__)

_helper_background_tasks: set[asyncio.Task[Any]] = set()


async def safe_edit_message(
    target: Message | CallbackQuery | None,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = "HTML",
) -> bool:
    """Safely edit text or caption of a Telegram Message or CallbackQuery.

    Handles photo vs text messages, suppresses 'message is not modified' Telegram errors,
    and guards against InaccessibleMessage / None objects.
    """
    if target is None:
        return False

    message: Message | None = None
    if isinstance(target, CallbackQuery):
        if isinstance(target.message, Message):
            message = target.message
        else:
            logger.debug("Cannot edit message: callback.message is not accessible Message")
            return False
    elif isinstance(target, Message):
        message = target
    else:
        return False

    if reply_markup and hasattr(reply_markup, "inline_keyboard"):
        for row in reply_markup.inline_keyboard:
            for btn in row:
                if btn.callback_data:
                    raw_bytes = btn.callback_data.encode("utf-8")
                    if len(raw_bytes) > 64:
                        logger.warning(
                            "Callback data exceeds 64 bytes (%d bytes): '%s'. Truncating to 64 bytes.",
                            len(raw_bytes),
                            btn.callback_data,
                        )
                        btn.callback_data = raw_bytes[:64].decode("utf-8", "ignore")

    try:
        if message.photo:
            await message.edit_caption(
                caption=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
        else:
            await message.edit_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
        return True
    except TelegramBadRequest as e:
        err_msg = str(e).lower()
        if "message is not modified" in err_msg:
            return True
        if "there is no text in the message to edit" in err_msg:
            try:
                await message.edit_caption(
                    caption=text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                )
                return True
            except TelegramBadRequest:
                pass
        logger.warning("TelegramBadRequest in safe_edit_message: %s", e)
        return False
    except Exception as e:  # noqa: BLE001
        logger.warning("Unexpected error in safe_edit_message: %s", e)
        return False


async def fetch_legacy_photo_bytes(file_id: str) -> bytes | None:
    """Download photo bytes from Telegram servers using legacy bot token."""
    old_token = getattr(settings, "OLD_BOT_TOKEN", None)
    if not old_token or not file_id:
        return None

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = True
    ssl_ctx.verify_mode = ssl.CERT_REQUIRED

    connector = aiohttp.TCPConnector(ssl=ssl_ctx)
    try:
        async with aiohttp.ClientSession(connector=connector) as session:
            info_url = f"https://api.telegram.org/bot{old_token}/getFile?file_id={file_id}"
            async with session.get(info_url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                if not data.get("ok"):
                    return None
                file_path = data.get("result", {}).get("file_path")
                if not file_path:
                    return None

            dl_url = f"https://api.telegram.org/file/bot{old_token}/{file_path}"
            async with session.get(dl_url, timeout=aiohttp.ClientTimeout(total=15)) as dl_resp:
                if dl_resp.status == 200:
                    return await dl_resp.read()
    except Exception as exc:
        logger.warning("fetch_legacy_photo_bytes error for file_id %s: %s", file_id, exc)
        return None
    return None


async def update_ad_photo_file_id(ad_id: int, new_file_id: str) -> None:
    """Persist recovered photo_file_id into database so future requests are native."""
    try:
        from sqlalchemy import update
        from src.database.base import async_session_maker
        from src.database.models import Ad

        async with async_session_maker() as session:
            await session.execute(
                update(Ad).where(Ad.id == ad_id).values(photo_file_id=new_file_id)
            )
            await session.commit()
            logger.info("Auto-migrated ad #%d photo_file_id to new bot token", ad_id)
    except Exception as exc:
        logger.warning("Failed to auto-update ad #%d photo_file_id: %s", ad_id, exc)


async def safe_send_photo(
    bot: Bot | Any,
    chat_id: int | str,
    photo: str,
    caption: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = "HTML",
    ad_id: int | None = None,
) -> Message | None:
    """Safely send photo with automatic legacy recovery and text fallback if photo_file_id is invalid."""
    try:
        return await bot.send_photo(
            chat_id=chat_id,
            photo=photo,
            caption=caption,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
    except (TelegramForbiddenError, TelegramRetryAfter):
        # Allow caller to handle deactivated users and flood limits
        raise
    except TelegramBadRequest as exc:
        err_msg = str(exc).lower()
        if "chat not found" in err_msg or "user not found" in err_msg:
            raise
        logger.warning(
            "safe_send_photo failed with TelegramBadRequest for chat %s: %s. Attempting legacy recovery...",
            chat_id,
            exc,
        )
        # Attempt automatic recovery using legacy bot token
        legacy_bytes = await fetch_legacy_photo_bytes(photo)
        if legacy_bytes:
            try:
                sent_msg = await bot.send_photo(
                    chat_id=chat_id,
                    photo=BufferedInputFile(legacy_bytes, filename="weapon.jpg"),
                    caption=caption,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                )
                if sent_msg and sent_msg.photo and ad_id:
                    new_file_id = sent_msg.photo[-1].file_id
                    t = asyncio.create_task(update_ad_photo_file_id(ad_id, new_file_id))
                    _helper_background_tasks.add(t)
                    t.add_done_callback(_helper_background_tasks.discard)
                return sent_msg
            except (TelegramForbiddenError, TelegramRetryAfter):
                raise
            except Exception as upload_err:
                logger.warning("Failed sending recovered photo: %s", upload_err)

        fallback_text = f"📷 <b>[Фото лота недоступно]</b>\n\n{caption}"
        try:
            return await bot.send_message(
                chat_id=chat_id,
                text=fallback_text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
        except (TelegramForbiddenError, TelegramRetryAfter):
            raise
        except Exception as send_err:
            logger.error("safe_send_photo fallback send_message also failed for chat %s: %s", chat_id, send_err)
            return None
    except Exception as exc:
        logger.error("safe_send_photo unexpected error for chat %s: %s", chat_id, exc)
        return None


async def safe_answer_photo(
    message: Message,
    photo: str,
    caption: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = "HTML",
    ad_id: int | None = None,
) -> Message | None:
    """Safely answer with photo or fallback to text message if photo_file_id is invalid."""
    try:
        return await message.answer_photo(
            photo=photo,
            caption=caption,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
    except TelegramBadRequest as exc:
        logger.warning("safe_answer_photo failed: %s. Attempting legacy recovery...", exc)
        legacy_bytes = await fetch_legacy_photo_bytes(photo)
        if legacy_bytes:
            try:
                sent_msg = await message.answer_photo(
                    photo=BufferedInputFile(legacy_bytes, filename="weapon.jpg"),
                    caption=caption,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                )
                if sent_msg and sent_msg.photo and ad_id:
                    new_file_id = sent_msg.photo[-1].file_id
                    t = asyncio.create_task(update_ad_photo_file_id(ad_id, new_file_id))
                    _helper_background_tasks.add(t)
                    t.add_done_callback(_helper_background_tasks.discard)
                return sent_msg
            except Exception as upload_err:
                logger.warning("Failed answering recovered photo: %s", upload_err)

        fallback_text = f"📷 <b>[Фото лота недоступно]</b>\n\n{caption}"
        return await message.answer(
            text=fallback_text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
    except Exception as exc:
        logger.error("safe_answer_photo unexpected error: %s", exc)
        return None

