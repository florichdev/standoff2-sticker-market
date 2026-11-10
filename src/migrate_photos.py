"""One-shot migration utility to migrate legacy ad photos from previous bot token to the new bot token."""

import asyncio
import io
import logging
import ssl
import sys

import aiohttp
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile
from sqlalchemy import select, update

from src.config import settings
from src.database.base import async_session_maker
from src.database.models import Ad

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("migrate_photos")


async def download_legacy_photo(old_token: str, file_id: str) -> bytes | None:
    """Download image bytes from Telegram servers using the legacy bot token."""
    ssl_ctx = ssl.create_default_context()
    connector = aiohttp.TCPConnector(ssl=ssl_ctx)
    try:
        async with aiohttp.ClientSession(connector=connector) as session:
            info_url = f"https://api.telegram.org/bot{old_token}/getFile?file_id={file_id}"
            async with session.get(info_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                if not data.get("ok"):
                    return None
                file_path = data.get("result", {}).get("file_path")
                if not file_path:
                    return None

            dl_url = f"https://api.telegram.org/file/bot{old_token}/{file_path}"
            async with session.get(dl_url, timeout=aiohttp.ClientTimeout(total=20)) as dl_resp:
                if dl_resp.status == 200:
                    return await dl_resp.read()
    except Exception as exc:
        logger.warning("Failed to download legacy file %s: %s", file_id, exc)
        return None
    return None


async def run_migration() -> None:
    """Migrate all legacy ad photos to the active bot token."""
    new_token = settings.BOT_TOKEN
    old_token = getattr(settings, "OLD_BOT_TOKEN", None)

    if not new_token:
        logger.error("BOT_TOKEN is not configured in .env!")
        return

    if not old_token:
        logger.error("OLD_BOT_TOKEN is not configured in .env! Cannot migrate legacy photos.")
        return

    # Check if a custom chat_id was passed via command line (--chat-id 123456)
    cli_chat_id = None
    for i, arg in enumerate(sys.argv):
        if arg in ("--chat-id", "-c") and i + 1 < len(sys.argv):
            try:
                cli_chat_id = int(sys.argv[i + 1])
            except ValueError:
                pass

    logger.info("Initializing bot with current token: %s...", new_token[:10])
    bot = Bot(token=new_token)

    try:
        me = await bot.get_me()
        logger.info("Current bot: @%s (ID: %d)", me.username, me.id)
    except Exception as exc:
        logger.error("Failed to connect to Telegram with BOT_TOKEN: %s", exc)
        await bot.session.close()
        return

    async with async_session_maker() as session:
        from src.database.models import User
        ads_res = await session.execute(select(Ad).order_by(Ad.id.asc()))
        ads = list(ads_res.scalars().all())

        users_res = await session.execute(select(User.id).order_by(User.id.desc()))
        db_user_ids = [r[0] for r in users_res.fetchall() if r[0]]

    # Candidate chats to receive upload: CLI arg > Admins > Users
    base_candidates: list[int] = []
    if cli_chat_id:
        base_candidates.append(cli_chat_id)
    for aid in settings.ADMIN_IDS:
        if aid not in base_candidates:
            base_candidates.append(aid)
    for uid in db_user_ids:
        if uid not in base_candidates:
            base_candidates.append(uid)

    logger.info("Found %d total ads in database to check.", len(ads))
    logger.info("Available candidate chats for photo upload: %s", base_candidates[:5])

    migrated_count = 0
    already_valid_count = 0
    failed_count = 0
    working_chat: int | None = None

    for ad in ads:
        if not ad.photo_file_id:
            continue

        # Check if the photo is already valid for current bot
        try:
            tg_file = await bot.get_file(ad.photo_file_id)
            if tg_file and tg_file.file_path:
                already_valid_count += 1
                continue
        except TelegramBadRequest:
            pass
        except Exception as exc:
            logger.debug("get_file error for ad #%d: %s", ad.id, exc)

        logger.info("Ad #%d ('%s | %s') has legacy photo_file_id. Migrating...", ad.id, ad.weapon_model, ad.skin_name)
        photo_bytes = await download_legacy_photo(old_token, ad.photo_file_id)
        if not photo_bytes:
            logger.error("Could not download legacy photo for ad #%d", ad.id)
            failed_count += 1
            continue

        new_file_id = None
        # Try candidate chats (or seller) to upload photo and get new file_id
        candidates_to_try = [working_chat] if working_chat else [ad.seller_id, *base_candidates]

        for target_chat in candidates_to_try:
            if not target_chat:
                continue
            try:
                msg = await bot.send_photo(
                    chat_id=target_chat,
                    photo=BufferedInputFile(photo_bytes, filename=f"ad_{ad.id}.jpg"),
                    caption=f"🔄 [Служебное] Обновление фото лота #{ad.id} ({ad.weapon_model} | {ad.skin_name})",
                )
                if msg and msg.photo:
                    new_file_id = msg.photo[-1].file_id
                    working_chat = target_chat
                    # Cleanup service message
                    try:
                        await bot.delete_message(chat_id=target_chat, message_id=msg.message_id)
                    except Exception:
                        pass
                    break
            except TelegramBadRequest as exc:
                if "chat not found" in str(exc).lower():
                    continue
                logger.error("TelegramBadRequest uploading to chat %s: %s", target_chat, exc)
            except Exception as exc:
                logger.error("Failed to re-upload photo to chat %s: %s", target_chat, exc)

        if new_file_id:
            async with async_session_maker() as session:
                await session.execute(
                    update(Ad).where(Ad.id == ad.id).values(photo_file_id=new_file_id)
                )
                await session.commit()
            logger.info("Ad #%d migrated successfully! New file_id: %s", ad.id, new_file_id[:20])
            migrated_count += 1
        else:
            logger.warning(
                "Could not migrate ad #%d: ни один пользователь еще не нажал /start в @Standstickerbot.",
                ad.id,
            )
            failed_count += 1

    await bot.session.close()
    logger.info(
        "Migration finished: %d migrated, %d already valid, %d failed.",
        migrated_count,
        already_valid_count,
        failed_count,
    )
    if failed_count > 0:
        logger.info(
            "Подсказка: Откройте Telegram, зайдите в @Standstickerbot и нажмите /start. "
            "После этого запустите скрипт повторно или передайте ваш ID: python -m src.migrate_photos --chat-id ВАШ_ID"
        )


if __name__ == "__main__":
    asyncio.run(run_migration())
