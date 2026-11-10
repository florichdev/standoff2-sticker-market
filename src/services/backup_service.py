import asyncio
import json
import logging
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.engine.url import make_url

from src.config import settings
from src.database.base import async_session_maker
from src.database.models import (
    Ad,
    ChatMessage,
    ChatSession,
    Report,
    SearchSubscription,
    StarTransaction,
    User,
    Referral,
)

logger = logging.getLogger(__name__)

BACKUP_DIR = Path("db_backup")


async def _backup_sqlite(db_path_str: str, temp_target: Path) -> bool:
    """Safely copy SQLite database file."""
    db_file = Path(db_path_str)
    if not db_file.is_file():
        logger.error("SQLite database file not found at %s", db_file)
        return False
    try:
        await asyncio.to_thread(shutil.copy2, db_file, temp_target)
        return True
    except Exception as exc:
        logger.error("SQLite backup failed: %s", exc)
        return False


async def _backup_postgres_pgdump(
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
    temp_target: Path,
) -> bool:
    """Attempt pg_dump binary execution if available."""
    env = os.environ.copy()
    if password:
        env["PGPASSWORD"] = password

    cmd = [
        "pg_dump",
        "-h", host or "localhost",
        "-p", str(port or 5432),
        "-U", user or "postgres",
        "-d", database,
        "--clean",
        "--if-exists",
        "--no-owner",
        "--no-privileges",
        "-f", str(temp_target),
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode == 0 and temp_target.is_file() and temp_target.stat().st_size > 0:
            logger.info("PostgreSQL pg_dump backup succeeded via binary.")
            return True
        logger.warning(
            "pg_dump returned non-zero code %s: %s. Falling back to internal dump.",
            proc.returncode,
            stderr.decode("utf-8", "ignore"),
        )
    except FileNotFoundError:
        logger.info("pg_dump binary not found in PATH; using internal SQLAlchemy table exporter.")
    except Exception as exc:
        logger.warning("pg_dump invocation failed: %s; using internal exporter.", exc)
    return False


async def _backup_sqlalchemy_tables(temp_target: Path) -> bool:
    """Pure-Python fallback: export all model records into structured JSON backup."""
    tables_data: dict[str, list[dict[str, object]]] = {}
    try:
        async with async_session_maker() as session:
            # Users
            res = await session.execute(select(User))
            tables_data["users"] = [
                {
                    "id": u.id,
                    "username": u.username,
                    "first_name": u.first_name,
                    "registration_date": u.registration_date.isoformat() if u.registration_date else None,
                    "is_banned": u.is_banned,
                    "is_active": u.is_active,
                    "rating": u.rating,
                    "ratings_count": u.ratings_count,
                    "free_top_boosts": getattr(u, "free_top_boosts", 0),
                }
                for u in res.scalars().all()
            ]

            # Ads
            res = await session.execute(select(Ad))
            tables_data["ads"] = [
                {
                    "id": a.id,
                    "seller_id": a.seller_id,
                    "category": a.category,
                    "weapon_model": a.weapon_model,
                    "skin_name": a.skin_name,
                    "sticker_count": a.sticker_count,
                    "sticker_rarity": a.sticker_rarity,
                    "price_gold": a.price_gold,
                    "photo_file_id": a.photo_file_id,
                    "views_count": a.views_count,
                    "clicks_count": a.clicks_count,
                    "is_top": a.is_top,
                    "top_until": a.top_until.isoformat() if a.top_until else None,
                    "created_at": a.created_at.isoformat() if a.created_at else None,
                    "is_active": a.is_active,
                }
                for a in res.scalars().all()
            ]

            # Chat Sessions
            res = await session.execute(select(ChatSession))
            tables_data["chat_sessions"] = [
                {
                    "id": c.id,
                    "ad_id": c.ad_id,
                    "buyer_id": c.buyer_id,
                    "seller_id": c.seller_id,
                    "is_active": c.is_active,
                    "created_at": c.created_at.isoformat() if c.created_at else None,
                    "closed_at": c.closed_at.isoformat() if c.closed_at else None,
                }
                for c in res.scalars().all()
            ]

            # Chat Messages
            res = await session.execute(select(ChatMessage))
            tables_data["chat_messages"] = [
                {
                    "id": m.id,
                    "chat_id": m.chat_id,
                    "sender_id": m.sender_id,
                    "content_type": m.content_type,
                    "text_content": m.text_content,
                    "file_id": m.file_id,
                    "sent_at": m.sent_at.isoformat() if m.sent_at else None,
                }
                for m in res.scalars().all()
            ]

            # Reports
            res = await session.execute(select(Report))
            tables_data["reports"] = [
                {
                    "id": r.id,
                    "reporter_id": r.reporter_id,
                    "violator_id": r.violator_id,
                    "ad_id": r.ad_id,
                    "chat_id": r.chat_id,
                    "reason": r.reason,
                    "status": r.status,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in res.scalars().all()
            ]

            # Star Transactions
            res = await session.execute(select(StarTransaction))
            tables_data["star_transactions"] = [
                {
                    "id": st.id,
                    "user_id": st.user_id,
                    "ad_id": st.ad_id,
                    "tariff": st.tariff,
                    "stars_amount": st.stars_amount,
                    "telegram_payment_charge_id": st.telegram_payment_charge_id,
                    "created_at": st.created_at.isoformat() if st.created_at else None,
                }
                for st in res.scalars().all()
            ]

            # Search Subscriptions
            res = await session.execute(select(SearchSubscription))
            tables_data["subscriptions"] = [
                {
                    "id": s.id,
                    "user_id": s.user_id,
                    "category": s.category,
                    "weapon_model": s.weapon_model,
                    "skin_name": s.skin_name,
                    "sticker_count": s.sticker_count,
                    "sticker_rarity": s.sticker_rarity,
                    "created_at": s.created_at.isoformat() if s.created_at else None,
                }
                for s in res.scalars().all()
            ]

            # Referrals
            res = await session.execute(select(Referral))
            tables_data["referrals"] = [
                {
                    "id": ref.id,
                    "referrer_id": ref.referrer_id,
                    "referred_id": ref.referred_id,
                    "created_at": ref.created_at.isoformat() if ref.created_at else None,
                }
                for ref in res.scalars().all()
            ]

        payload = {
            "backup_version": "1.0",
            "created_at": datetime.now(UTC).isoformat(),
            "data": tables_data,
        }

        with open(temp_target, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return True
    except Exception as exc:
        logger.error("Internal table exporter backup failed: %s", exc)
        return False


async def perform_database_backup() -> bool:
    """Execute daily database backup and retain strictly one fresh backup file in db_backup/."""
    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.error("Cannot create backup directory %s: %s", BACKUP_DIR, exc)
        return False

    url_str = settings.DATABASE_URL
    parsed = make_url(url_str)
    backend_name = parsed.get_backend_name()

    temp_file = BACKUP_DIR / f"_temp_backup_{int(datetime.now(UTC).timestamp())}.tmp"
    success = False
    final_ext = "sql"

    if backend_name == "sqlite":
        final_ext = "sqlite"
        db_path = parsed.database or "standoff_market.db"
        success = await _backup_sqlite(db_path, temp_file)
    else:
        # PostgreSQL / other SQL backends
        final_ext = "sql"
        host = parsed.host or "localhost"
        port = parsed.port or 5432
        user = parsed.username or "postgres"
        password = parsed.password or ""
        database = parsed.database or "standoff_market"

        success = await _backup_postgres_pgdump(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            temp_target=temp_file,
        )

        if not success or not temp_file.is_file() or temp_file.stat().st_size == 0:
            final_ext = "json"
            success = await _backup_sqlalchemy_tables(temp_file)

    if not success or not temp_file.is_file() or temp_file.stat().st_size == 0:
        logger.error("Database backup creation failed; previous backup preserved.")
        if temp_file.is_file():
            temp_file.unlink(missing_ok=True)
        return False

    target_name = f"standoff_market_backup_latest.{final_ext}"
    target_file = BACKUP_DIR / target_name

    # Atomic rotation: remove old backup files so only ONE fresh backup exists
    try:
        for old_item in BACKUP_DIR.iterdir():
            if old_item.is_file() and old_item != temp_file:
                try:
                    old_item.unlink()
                except OSError as err:
                    logger.warning("Failed to remove old backup %s: %s", old_item, err)

        temp_file.replace(target_file)
        file_size = target_file.stat().st_size
        logger.info(
            "24h Database backup created: %s (%d bytes). Old backup cleaned. Exactly 1 backup retained in %s.",
            target_file.name,
            file_size,
            BACKUP_DIR,
        )
        return True
    except Exception as exc:
        logger.error("Failed to finalize backup rotation: %s", exc)
        return False
