import asyncio
import base64
import hashlib
import hmac
import io
import json
import logging
import time
from contextlib import asynccontextmanager
from html import escape
from pathlib import Path
from typing import Any

import fastapi
import uvicorn
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from fastapi import (
    Cookie,
    Depends,
    Form,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi import (
    Path as FastApiPath,
)
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update

from src.config import settings
from src.data.game_catalog import (
    GAME_CATALOG,
    add_category,
    add_custom_skin,
    add_skin,
    add_weapon,
    delete_category,
    delete_custom_skin,
    delete_skin,
    delete_weapon,
    edit_category,
    edit_skin,
    edit_weapon,
    get_categories,
    get_custom_skins,
    reset_catalog_to_default,
    sync_catalog_if_modified,
)
from src.data.settings_manager import (
    get_autopost_channel_id,
    get_market_settings,
    get_max_active_ads,
    is_autopost_enabled,
    update_market_settings,
)
from src.database.base import async_session_maker, close_db
from src.database.models import Ad, Referral, User
from src.database.repositories.admin_repo import AdminRepository
from src.database.repositories.referral_repo import ReferralRepository
from src.database.repositories.user_repo import UserRepository
from src.services.autopost import schedule_ad_broadcast_cleanup
from src.services.broadcast import broadcast_worker
from src.services.logger_service import (
    count_log_sources,
    get_log_file_path,
    list_available_log_dates,
    read_parsed_logs,
    setup_app_logging,
)

logger = logging.getLogger("src.web")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

_background_tasks: set[asyncio.Task[Any]] = set()


@asynccontextmanager
async def lifespan(app: fastapi.FastAPI):
    """Manage application lifespan and maintain a single Bot connection pool."""
    setup_app_logging(logging.INFO)
    if settings.BOT_TOKEN:
        bot_session = None
        if settings.TELEGRAM_PROXY:
            from aiogram.client.session.aiohttp import AiohttpSession
            bot_session = AiohttpSession(proxy=settings.TELEGRAM_PROXY)
        app.state.bot = Bot(
            token=settings.BOT_TOKEN,
            session=bot_session,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        try:
            bot_info = await app.state.bot.get_me()
            app.state.bot_username = bot_info.username or "Standstickerbot"
        except Exception:
            app.state.bot_username = "Standstickerbot"
    else:
        app.state.bot = None
        app.state.bot_username = "Standstickerbot"

    yield

    bot = getattr(app.state, "bot", None)
    if bot:
        await bot.session.close()
    await close_db()


app = fastapi.FastAPI(
    title="Standsticker Admin Hub",
    description="Web Control Panel for Standsticker Marketplace",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint verifying application and database connectivity."""
    try:
        async with async_session_maker() as session:
            await session.execute(select(1))
        return {"status": "ok", "database": "connected"}
    except Exception as e:
        logger.error("Health check failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database service unavailable",
        ) from e

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def get_bot_instance() -> Bot | None:
    """Retrieve singleton Telegram bot client from FastAPI application state."""
    bot = getattr(app.state, "bot", None)
    if bot is not None:
        return bot
    if not settings.BOT_TOKEN:
        return None
    bot_session = None
    if settings.TELEGRAM_PROXY:
        from aiogram.client.session.aiohttp import AiohttpSession
        bot_session = AiohttpSession(proxy=settings.TELEGRAM_PROXY)
    return Bot(
        token=settings.BOT_TOKEN,
        session=bot_session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )




_session_version: int = 1


def revoke_all_admin_sessions() -> None:
    """Increment session version to immediately invalidate all existing admin session tokens."""
    global _session_version
    _session_version += 1


def create_admin_session_token(expires_in: int = 86400) -> str:
    """Create an HMAC-SHA256 signed session token for admin authentication (24 hours)."""
    now = int(time.time())
    payload = {"sub": "admin", "iat": now, "exp": now + expires_in, "v": _session_version}
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    b64_payload = base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")
    signature = hmac.new(
        settings.SESSION_SECRET_KEY.encode("utf-8"),
        b64_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{b64_payload}.{signature}"


def verify_admin_session_token(token: str | None) -> bool:
    """Verify HMAC-SHA256 signature, session version, and expiration timestamp of session token."""
    if not token or "." not in token:
        return False
    parts = token.split(".")
    if len(parts) != 2:
        return False
    b64_payload, signature = parts
    expected_sig = hmac.new(
        settings.SESSION_SECRET_KEY.encode("utf-8"),
        b64_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected_sig):
        return False
    try:
        rem = len(b64_payload) % 4
        padded = b64_payload + ("=" * (4 - rem) if rem > 0 else "")
        raw = base64.urlsafe_b64decode(padded.encode("utf-8"))
        payload = json.loads(raw.decode("utf-8"))
        if payload.get("exp", 0) < int(time.time()):
            return False
        if payload.get("v") != _session_version:
            return False
        return bool(payload.get("sub") == "admin")
    except (ValueError, KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.debug("Session token decoding failed: %s", exc)
        return False


def require_admin(
    request: Request,
    admin_session: str | None = Cookie(default=None),
) -> bool:
    """Validate admin session token and enforce CSRF protection on mutating endpoints."""
    is_api = request.url.path.startswith("/admin/api/") or "application/json" in request.headers.get("accept", "")

    if not verify_admin_session_token(admin_session):
        if is_api:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Необходима авторизация. Сессия недействительна или истекла.",
            )
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": "/admin/login"},
        )

    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        sec_fetch_site = request.headers.get("sec-fetch-site")
        if sec_fetch_site and sec_fetch_site not in ("same-origin", "same-site", "none"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="CSRF защита: запрос от внешнего источника отклонён.",
            )
        origin = request.headers.get("origin")
        host = request.headers.get("host")
        if origin and host:
            origin_host = origin.split("://")[-1].rstrip("/")
            if origin_host != host:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="CSRF защита: несовпадение origin заголовка.",
                )

    return True




_login_failed_attempts: dict[str, list[float]] = {}
MAX_LOGIN_ATTEMPTS: int = 5
LOGIN_RATE_WINDOW_SECONDS: float = 60.0


def _get_client_ip(request: Request) -> str:
    """Extract client IP, taking into account X-Forwarded-For ONLY if BEHIND_PROXY is enabled."""
    if getattr(settings, "BEHIND_PROXY", False):
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"


def check_login_rate_limit(ip: str) -> tuple[bool, int]:
    """Check whether client IP has exceeded login brute-force limits.

    Returns (is_allowed, retry_after_seconds).
    """
    now = time.time()
    if len(_login_failed_attempts) > 1000:
        stale_keys = [
            k
            for k, v in _login_failed_attempts.items()
            if not v or (now - v[-1] >= LOGIN_RATE_WINDOW_SECONDS)
        ]
        for k in stale_keys:
            _login_failed_attempts.pop(k, None)

    attempts = _login_failed_attempts.get(ip, [])
    recent_attempts = [t for t in attempts if now - t < LOGIN_RATE_WINDOW_SECONDS]
    if recent_attempts:
        _login_failed_attempts[ip] = recent_attempts
    else:
        _login_failed_attempts.pop(ip, None)

    if len(recent_attempts) >= MAX_LOGIN_ATTEMPTS:
        retry_after = int(LOGIN_RATE_WINDOW_SECONDS - (now - recent_attempts[0])) + 1
        return False, max(retry_after, 1)
    return True, 0


def record_failed_login(ip: str) -> None:
    """Record a failed login attempt timestamp for client IP."""
    now = time.time()
    if ip not in _login_failed_attempts:
        _login_failed_attempts[ip] = []
    _login_failed_attempts[ip].append(now)


def reset_failed_logins(ip: str) -> None:
    """Reset failed login attempts on successful authorization."""
    _login_failed_attempts.pop(ip, None)


@app.get("/admin/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    admin_session: str | None = Cookie(default=None),
) -> Any:
    """Render admin login page."""
    if verify_admin_session_token(admin_session):
        return RedirectResponse(url="/admin/", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": None},
    )


@app.post("/admin/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    secret: str = Form(...),
) -> Any:
    """Process login form submission with brute-force rate-limiting and constant-time secret check."""
    client_ip = _get_client_ip(request)
    is_allowed, retry_after = check_login_rate_limit(client_ip)
    if not is_allowed:
        logger.warning("Admin login brute-force blocked for IP: %s", client_ip)
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "error": f"Слишком много неудачных попыток входа. Пожалуйста, подождите {retry_after} сек.",
            },
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={"Retry-After": str(retry_after)},
        )

    if hmac.compare_digest(
        secret.encode("utf-8"), settings.WEB_ADMIN_SECRET.encode("utf-8")
    ):
        reset_failed_logins(client_ip)
        response = RedirectResponse(url="/admin/", status_code=status.HTTP_302_FOUND)
        token = create_admin_session_token()
        response.set_cookie(
            key="admin_session",
            value=token,
            httponly=True,
            samesite="lax",
            secure=settings.WEB_ADMIN_SECURE_COOKIE,
            max_age=86400,  # 24 hours
        )
        return response

    record_failed_login(client_ip)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": "Неверный ключ доступа администратора"},
        status_code=status.HTTP_401_UNAUTHORIZED,
    )


@app.get("/admin/logout")
async def logout() -> Any:
    """Clear session cookie, revoke active sessions, and redirect to login."""
    revoke_all_admin_sessions()
    response = RedirectResponse(url="/admin/login", status_code=status.HTTP_302_FOUND)
    response.delete_cookie(
        key="admin_session",
        httponly=True,
        samesite="lax",
        secure=settings.WEB_ADMIN_SECURE_COOKIE,
    )
    return response




@app.get("/admin/", response_class=HTMLResponse)
async def dashboard_page(
    request: Request,
    auth_admin: bool = Depends(require_admin),
) -> Any:
    """Render main administrative dashboard."""
    _ = auth_admin
    async with async_session_maker() as session:
        repo = AdminRepository(session)
        stats = await repo.get_system_stats()
        recent_reports, _reports_total = await repo.get_pending_reports(limit=5)

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "active_page": "dashboard",
            "stats": stats,
            "recent_reports": recent_reports,
            "pending_reports_count": stats.get("reports_pending", 0),
        },
    )


@app.get("/admin/reports", response_class=HTMLResponse)
async def reports_page(
    request: Request,
    status: str = "pending",
    auth_admin: bool = Depends(require_admin),
) -> Any:
    """Render reports moderation queue and history."""
    _ = auth_admin
    async with async_session_maker() as session:
        repo = AdminRepository(session)
        stats = await repo.get_system_stats()
        reports, total = await repo.get_all_reports_filtered(status=status, limit=100)

    return templates.TemplateResponse(
        request=request,
        name="reports.html",
        context={
            "active_page": "reports",
            "reports": reports,
            "total": total,
            "status_filter": status,
            "pending_reports_count": stats.get("reports_pending", 0),
        },
    )


@app.get("/admin/users", response_class=HTMLResponse)
async def users_page(
    request: Request,
    q: str = "",
    auth_admin: bool = Depends(require_admin),
) -> Any:
    """Render users database, referral stats, and search interface."""
    _ = auth_admin
    async with async_session_maker() as session:
        repo = AdminRepository(session)
        stats = await repo.get_system_stats()
        users = await repo.get_users_list(search=q, limit=100)

        ref_repo = ReferralRepository(session)
        ref_system_stats = await ref_repo.get_system_referral_stats()

        user_ids = [u.id for u in users]
        referrals_count_map: dict[int, int] = {}
        invited_by_map: dict[int, tuple[int, str | None]] = {}

        if user_ids:
            stmt_counts = (
                select(Referral.referrer_id, func.count(Referral.id))
                .where(Referral.referrer_id.in_(user_ids))
                .group_by(Referral.referrer_id)
            )
            res_counts = await session.execute(stmt_counts)
            referrals_count_map = {row[0]: int(row[1]) for row in res_counts.all()}

            stmt_inviters = (
                select(Referral.referred_id, Referral.referrer_id, User.username)
                .join(User, User.id == Referral.referrer_id, isouter=True)
                .where(Referral.referred_id.in_(user_ids))
            )
            res_inviters = await session.execute(stmt_inviters)
            invited_by_map = {row[0]: (row[1], row[2]) for row in res_inviters.all()}

        for u in users:
            setattr(u, "referrals_count", referrals_count_map.get(u.id, 0))
            setattr(u, "invited_by", invited_by_map.get(u.id))

    return templates.TemplateResponse(
        request=request,
        name="users.html",
        context={
            "active_page": "users",
            "users": users,
            "query": q,
            "referral_stats": ref_system_stats,
            "pending_reports_count": stats.get("reports_pending", 0),
        },
    )


@app.get("/admin/ads", response_class=HTMLResponse)
async def ads_page(
    request: Request,
    filter: str | None = None,
    filter_type: str | None = None,
    seller_id: int | None = None,
    auth_admin: bool = Depends(require_admin),
) -> Any:
    """Render marketplace listings management page."""
    _ = auth_admin
    selected_filter = filter or filter_type or "active"
    async with async_session_maker() as session:
        repo = AdminRepository(session)
        stats = await repo.get_system_stats()
        ads = await repo.get_all_ads_filtered(
            filter_type=selected_filter, seller_id=seller_id, limit=100
        )

    return templates.TemplateResponse(
        request=request,
        name="ads.html",
        context={
            "active_page": "ads",
            "ads": ads,
            "filter_type": selected_filter,
            "seller_id": seller_id,
            "pending_reports_count": stats.get("reports_pending", 0),
        },
    )


@app.get("/admin/ads/{ad_id}", response_class=HTMLResponse)
async def ad_detail_page(
    request: Request,
    ad_id: int = FastApiPath(..., ge=1, le=2147483647, description="Listing ID"),
    auth_admin: bool = Depends(require_admin),
) -> Any:
    """Render detailed ad card formatted as Telegram post with management actions."""
    _ = auth_admin
    async with async_session_maker() as session:
        repo = AdminRepository(session)
        stats = await repo.get_system_stats()
        ad = await repo.get_ad_with_seller(ad_id)
        if not ad:
            raise HTTPException(status_code=404, detail="Объявление не найдено")
        reports = await repo.get_ad_reports(ad_id)
        seller_active_count = await repo.count_seller_active_ads(ad.seller_id)

    sticker_label = (
        "Без наклеек"
        if ad.sticker_count == 0
        else f"{ad.sticker_count} шт. • {ad.sticker_rarity}"
    )

    return templates.TemplateResponse(
        request=request,
        name="ad_detail.html",
        context={
            "active_page": "ads",
            "ad": ad,
            "reports": reports,
            "sticker_label": sticker_label,
            "seller_active_count": seller_active_count,
            "pending_reports_count": stats.get("reports_pending", 0),
        },
    )


@app.get("/admin/ads/{ad_id}/photo")
async def ad_photo_proxy(
    ad_id: int = FastApiPath(..., ge=1, le=2147483647, description="Listing ID"),
    auth_admin: bool = Depends(require_admin),
) -> Any:
    """Proxy Telegram photo file for web display without exposing bot token."""
    _ = auth_admin
    async with async_session_maker() as session:
        repo = AdminRepository(session)
        ad = await repo.get_ad_with_seller(ad_id)
        if not ad or not ad.photo_file_id:
            raise HTTPException(status_code=404, detail="Фото не найдено")
        photo_file_id = ad.photo_file_id

    bot = get_bot_instance()
    if not bot:
        raise HTTPException(status_code=503, detail="Telegram бот не инициализирован")

    try:
        tg_file = await bot.get_file(photo_file_id)
        if not tg_file.file_path:
            raise HTTPException(status_code=404, detail="Файл не найден на серверах Telegram")

        stream = io.BytesIO()
        await bot.download_file(tg_file.file_path, destination=stream)
        image_bytes = stream.getvalue()
    except Exception as exc:
        logger.warning("Bot get_file failed for ad %d, attempting legacy recovery: %s", ad_id, exc)
        from src.utils.helpers import fetch_legacy_photo_bytes
        image_bytes = await fetch_legacy_photo_bytes(photo_file_id)
        if not image_bytes:
            raise HTTPException(
                status_code=502, detail="Не удалось загрузить скриншот из Telegram"
            ) from exc

    return Response(
        content=image_bytes,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@app.get("/admin/broadcast", response_class=HTMLResponse)
async def broadcast_page(
    request: Request,
    auth_admin: bool = Depends(require_admin),
) -> Any:
    """Render broadcast composer interface quickly without blocking on external Telegram API."""
    _ = auth_admin
    async with async_session_maker() as session:
        repo = AdminRepository(session)
        stats = await repo.get_system_stats()
        active_ids = await repo.get_all_active_user_ids()

    return templates.TemplateResponse(
        request=request,
        name="broadcast.html",
        context={
            "active_page": "broadcast",
            "active_users_count": len(active_ids),
            "pending_reports_count": stats.get("reports_pending", 0),
            "bot_name": "Standsticker",
            "bot_username": getattr(request.app.state, "bot_username", "Standstickerbot"),
        },
    )


async def get_safe_pending_reports_count() -> int:
    """Fetch pending reports count safely with zero fallback if DB connection fails."""
    try:
        async with async_session_maker() as session:
            repo = AdminRepository(session)
            stats = await repo.get_system_stats()
            return int(stats.get("reports_pending", 0))
    except Exception:
        return 0


@app.get("/admin/catalog", response_class=HTMLResponse)
async def catalog_page(
    request: Request,
    auth_admin: bool = Depends(require_admin),
) -> Any:
    """Render skin and weapon catalog management page."""
    _ = auth_admin
    sync_catalog_if_modified()
    pending_count = await get_safe_pending_reports_count()
    categories = get_categories()
    custom_skins = get_custom_skins()
    total_weapons = sum(len(wep_dict) for wep_dict in GAME_CATALOG.values())
    total_skins = sum(
        len(skins)
        for wep_dict in GAME_CATALOG.values()
        for skins in wep_dict.values()
    )

    return templates.TemplateResponse(
        request=request,
        name="catalog.html",
        context={
            "active_page": "catalog",
            "categories": categories,
            "custom_skins": custom_skins,
            "total_categories": len(categories),
            "total_weapons": total_weapons,
            "total_skins": total_skins,
            "catalog_json": (
                json.dumps(GAME_CATALOG, ensure_ascii=False)
                .replace("<", "\\u003c")
                .replace(">", "\\u003e")
            ),
            "pending_reports_count": pending_count,
        },
    )


@app.get("/admin/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    auth_admin: bool = Depends(require_admin),
) -> Any:
    """Render marketplace settings configuration page."""
    _ = auth_admin
    pending_count = await get_safe_pending_reports_count()
    market_cfg = get_market_settings()
    server_info = {
        "host": settings.WEB_ADMIN_HOST,
        "port": settings.WEB_ADMIN_PORT,
        "environment": settings.ENVIRONMENT,
    }

    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "active_page": "settings",
            "settings_data": market_cfg,
            "server_info": server_info,
            "pending_reports_count": pending_count,
        },
    )


@app.get("/admin/logs", response_class=HTMLResponse)
async def logs_page(
    request: Request,
    date: str | None = None,
    level: str = "ALL",
    source: str = "all",
    q: str = "",
    auth_admin: bool = Depends(require_admin),
) -> Any:
    """Render system log viewer with daily file rotation, source tabs, and filters."""
    _ = auth_admin
    pending_count = await get_safe_pending_reports_count()
    available_dates = list_available_log_dates()
    selected_date = date if (date and date in available_dates) else available_dates[0]
    logs = read_parsed_logs(
        date_str=selected_date, level=level, search=q, source=source, limit=500
    )
    counts = count_log_sources(selected_date)

    return templates.TemplateResponse(
        request=request,
        name="logs.html",
        context={
            "active_page": "logs",
            "available_dates": available_dates,
            "selected_date": selected_date,
            "selected_level": level,
            "selected_source": source.lower(),
            "query": q,
            "logs": logs,
            "counts": counts,
            "pending_reports_count": pending_count,
        },
    )


@app.get("/admin/logs/download")
async def logs_download(
    date: str | None = None,
    source: str = "all",
    auth_admin: bool = Depends(require_admin),
) -> Any:
    """Export and download date-stamped .txt log file, optionally filtered by source (bot/web)."""
    _ = auth_admin
    file_path = get_log_file_path(date)
    if not file_path.is_file():
        raise HTTPException(
            status_code=404, detail="Файл логов за указанную дату не найден"
        )

    clean_date = file_path.stem.replace("app_", "")
    if source in ("bot", "web"):
        entries = read_parsed_logs(date_str=clean_date, source=source, limit=100000)
        content = "\n".join(e["raw"] for e in entries)
        filename = f"standoff_{source}_logs_{clean_date}.txt"
        return Response(
            content=content,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    filename = f"standoff_all_logs_{clean_date}.txt"
    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type="text/plain; charset=utf-8",
    )




class BroadcastPayload(BaseModel):
    text: str


@app.post("/admin/api/reports/{report_id}/resolve")
async def api_resolve_report(
    report_id: int,
    auth_admin: bool = Depends(require_admin),
) -> Any:
    """Resolve report, deactivate offending ad if applicable, and notify parties."""
    _ = auth_admin
    async with async_session_maker() as session:
        repo = AdminRepository(session)
        report = await repo.get_report_by_id(report_id)
        if not report:
            raise HTTPException(status_code=404, detail="Жалоба не найдена")

        ad_deactivated = False
        if report.ad_id:
            ad = await repo.get_ad_with_seller(report.ad_id)
            if ad and ad.seller_id == report.violator_id and ad.is_active:
                await repo.deactivate_ad_as_admin(report.ad_id)
                ad_deactivated = True

        await repo.resolve_report(report_id, status="resolved")
        await session.commit()

        bot = get_bot_instance()
        if bot and ad_deactivated and report.ad_id:
            schedule_ad_broadcast_cleanup(bot, [report.ad_id])

        if bot:
            async def notify_parties() -> None:
                try:
                    lot_info = f" по лоту #{report.ad_id}" if (report.ad_id and ad_deactivated) else ""
                    context_info = f" в диалоге сделки #{report.chat_id}" if (report.chat_id and not ad_deactivated) else ""
                    warn_text = (
                        "⚠️ <b>Официальное предупреждение от администрации</b>\n\n"
                        f"На вас поступила обоснованная жалоба{lot_info or context_info}.\n"
                        f"{'Связанное объявление было автоматически снято с продажи.\n' if ad_deactivated else ''}"
                        "Пожалуйста, соблюдайте правила честной торговли на маркете.\n\n"
                        "❗️ <b>Внимание:</b> при повторных нарушениях ваш профиль будет заблокирован навсегда."
                    )
                    await bot.send_message(chat_id=report.violator_id, text=warn_text)
                except TelegramAPIError:
                    pass

                if report.reporter_id:
                    try:
                        reporter_msg = (
                            "✅ <b>Ваша жалоба была рассмотрена</b>\n\n"
                            f"Администрация проверила жалобу #{report_id} и приняла меры: "
                            f"нарушителю вынесено предупреждение{', а лот снят с публикации' if ad_deactivated else ''}.\n\n"
                            "Спасибо за содействие безопасности маркета!"
                        )
                        await bot.send_message(chat_id=report.reporter_id, text=reporter_msg)
                    except TelegramAPIError:
                        pass

            task = asyncio.create_task(notify_parties())
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)

    return {"ok": True, "report_id": report_id, "ad_deactivated": ad_deactivated}


@app.post("/admin/api/reports/{report_id}/dismiss")
async def api_dismiss_report(
    report_id: int,
    auth_admin: bool = Depends(require_admin),
) -> Any:
    """Dismiss report."""
    _ = auth_admin
    async with async_session_maker() as session:
        repo = AdminRepository(session)
        success = await repo.resolve_report(report_id, status="dismissed")
        await session.commit()
    if not success:
        raise HTTPException(status_code=404, detail="Жалоба не найдена")
    return {"ok": True, "report_id": report_id}


@app.get("/admin/api/chats/{chat_id}/logs")
async def api_chat_logs(
    chat_id: int,
    auth_admin: bool = Depends(require_admin),
) -> Any:
    """Fetch audit history for a chat session."""
    _ = auth_admin
    async with async_session_maker() as session:
        repo = AdminRepository(session)
        chat = await repo.get_chat_session(chat_id)
        if not chat:
            raise HTTPException(status_code=404, detail="Чат не найден")

        messages = await repo.get_chat_logs(chat_id)
        seller_id = chat.seller_id

        output = []
        for m in messages:
            sender_role = "seller" if m.sender_id == seller_id else "buyer"
            output.append(
                {
                    "id": m.id,
                    "sender_id": m.sender_id,
                    "sender_role": sender_role,
                    "text_content": m.text_content,
                    "sent_at": m.sent_at.isoformat() if m.sent_at else "",
                }
            )

    return output


@app.post("/admin/api/users/{user_id}/ban")
async def api_ban_user_cascade(
    user_id: int = FastApiPath(..., ge=1, description="Telegram User ID"),
    auth_admin: bool = Depends(require_admin),
) -> Any:
    """Cascade ban user, deactivate ads, close active chats and notify via bot."""
    _ = auth_admin
    if user_id in settings.ADMIN_IDS:
        raise HTTPException(
            status_code=400, detail="Нельзя заблокировать администратора платформы!"
        )

    async with async_session_maker() as session:
        repo = AdminRepository(session)
        success, ads_count, partner_ids, deactivated_ads = await repo.ban_user_cascade(user_id)
        await session.commit()

    if not success:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    bot = get_bot_instance()
    if bot:
        if deactivated_ads:
            schedule_ad_broadcast_cleanup(bot, deactivated_ads)

        async def send_notifications() -> None:
            try:
                await bot.send_message(
                    chat_id=user_id,
                    text="⛔️ <b>Ваш аккаунт заблокирован администратором маркетплейса</b> за нарушение правил платформы.",
                )
            except TelegramAPIError:
                pass

            for partner_id in partner_ids:
                try:
                    await bot.send_message(
                        chat_id=partner_id,
                        text="⚠️ <b>Диалог принудительно закрыт</b>: ваш собеседник был заблокирован администрацией маркетплейса за нарушение правил.",
                    )
                except TelegramAPIError:
                    pass

        task = asyncio.create_task(send_notifications())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    return {
        "ok": True,
        "user_id": user_id,
        "ads_deactivated": ads_count,
        "partners_notified": len(partner_ids),
    }


@app.post("/admin/api/users/{user_id}/unban")
async def api_unban_user(
    user_id: int = FastApiPath(..., ge=1, description="Telegram User ID"),
    auth_admin: bool = Depends(require_admin),
) -> Any:
    """Unban user."""
    _ = auth_admin
    async with async_session_maker() as session:
        repo = AdminRepository(session)
        success = await repo.unban_user(user_id)
        await session.commit()

    if not success:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    bot = get_bot_instance()
    if bot:

        async def send_unban_notice() -> None:
            try:
                await bot.send_message(
                    chat_id=user_id,
                    text="✅ <b>Ваш аккаунт был разблокирован администратором</b>. Соблюдайте правила платформы!",
                )
            except TelegramAPIError:
                pass

        task = asyncio.create_task(send_unban_notice())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    return {"ok": True, "user_id": user_id}


@app.post("/admin/api/users/{user_id}/delete")
async def api_delete_user(
    user_id: int = FastApiPath(..., ge=1, description="Telegram User ID"),
    auth_admin: bool = Depends(require_admin),
) -> Any:
    """Completely delete user and all associated records from database."""
    _ = auth_admin
    if user_id in settings.ADMIN_IDS:
        raise HTTPException(
            status_code=400, detail="Нельзя удалить администратора платформы!"
        )

    async with async_session_maker() as session:
        user_repo = UserRepository(session)
        success, partner_ids = await user_repo.delete_permanently(user_id)

    if not success:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    bot = get_bot_instance()
    if bot and partner_ids:
        async def send_partner_notices() -> None:
            for pid in partner_ids:
                try:
                    await bot.send_message(
                        chat_id=pid,
                        text="⚠️ <b>Диалог закрыт</b>: аккаунт вашего собеседника был удален из маркетплейса.",
                    )
                except TelegramAPIError:
                    pass

        task = asyncio.create_task(send_partner_notices())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    return {"ok": True, "user_id": user_id}


@app.post("/admin/api/ads/{ad_id}/deactivate")
async def api_deactivate_ad(
    ad_id: int = FastApiPath(..., ge=1, le=2147483647, description="Listing ID"),
    auth_admin: bool = Depends(require_admin),
) -> Any:
    """Deactivate listing."""
    _ = auth_admin
    async with async_session_maker() as session:
        repo = AdminRepository(session)
        success = await repo.deactivate_ad_as_admin(ad_id)
        await session.commit()

    if not success:
        raise HTTPException(status_code=404, detail="Объявление не найдено")

    bot = get_bot_instance()
    if bot:
        schedule_ad_broadcast_cleanup(bot, [ad_id])

    return {"ok": True, "ad_id": ad_id}


@app.post("/admin/api/ads/{ad_id}/activate")
async def api_activate_ad(
    ad_id: int = FastApiPath(..., ge=1, le=2147483647, description="Listing ID"),
    auth_admin: bool = Depends(require_admin),
) -> Any:
    """Reactivate previously deactivated ad with seller limit verification."""
    _ = auth_admin
    async with async_session_maker() as session:
        repo = AdminRepository(session)
        max_limit = get_max_active_ads()
        success, message, active_count = await repo.activate_ad_as_admin(
            ad_id, max_limit=max_limit
        )
        if not success:
            raise HTTPException(status_code=400, detail=message)
        await session.commit()

    return {
        "ok": True,
        "ad_id": ad_id,
        "message": message,
        "active_count": active_count,
    }


@app.post("/admin/api/ads/{ad_id}/toggle-top")
async def api_toggle_top_ad(
    ad_id: int = FastApiPath(..., ge=1, le=2147483647, description="Listing ID"),
    auth_admin: bool = Depends(require_admin),
) -> Any:
    """Toggle TOP promotion status for an ad."""
    _ = auth_admin
    async with async_session_maker() as session:
        repo = AdminRepository(session)
        success, is_top = await repo.toggle_ad_top_as_admin(ad_id, duration_days=3)
        await session.commit()

    if not success:
        raise HTTPException(status_code=404, detail="Объявление не найдено")

    return {"ok": True, "ad_id": ad_id, "is_top": is_top}


class WarnUserPayload(BaseModel):
    reason: str = "Нарушение правил торговой площадки"
    ad_id: int | None = Field(default=None, ge=1, le=2147483647)


@app.post("/admin/api/users/{user_id}/warn")
async def api_warn_user(
    payload: WarnUserPayload,
    user_id: int = FastApiPath(..., ge=1, description="Telegram User ID"),
    auth_admin: bool = Depends(require_admin),
) -> Any:
    """Send an official administrative warning to a user via Telegram bot."""
    _ = auth_admin
    bot = get_bot_instance()
    if not bot:
        raise HTTPException(status_code=503, detail="Telegram бот не настроен")

    lot_info = f" по лоту #{payload.ad_id}" if payload.ad_id else ""
    reason_clean = escape(payload.reason.strip() or "Нарушение правил торговой площадки")
    text = (
        "⚠️ <b>Официальное предупреждение от администрации Standsticker</b>\n\n"
        f"Вам вынесено предупреждение{lot_info}.\n"
        f"<b>Причина:</b> {reason_clean}\n\n"
        "Пожалуйста, соблюдайте правила честной торговли.\n"
        "❗️ <b>Внимание:</b> повторные нарушения приведут к блокировке аккаунта."
    )
    try:
        await bot.send_message(chat_id=user_id, text=text)
    except TelegramAPIError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Не удалось отправить предупреждение: {exc}",
        ) from exc

    return {"ok": True, "user_id": user_id}


@app.post("/admin/api/broadcast")
async def api_broadcast(
    payload: BroadcastPayload,
    auth_admin: bool = Depends(require_admin),
) -> Any:
    """Launch mass broadcast in background task without blocking HTTP request."""
    _ = auth_admin
    text = payload.text.strip()
    if not text:
        raise HTTPException(
            status_code=400, detail="Текст сообщения не может быть пустым"
        )

    bot = get_bot_instance()
    if not bot:
        raise HTTPException(
            status_code=400,
            detail="Токен Telegram бота не настроен (BOT_TOKEN пустой в .env)",
        )

    admin_id = settings.ADMIN_IDS[0] if settings.ADMIN_IDS else 0

    async with async_session_maker() as session:
        repo = AdminRepository(session)
        total_recipients = len(await repo.get_all_active_user_ids())

    broadcast_task = asyncio.create_task(
        broadcast_worker(
            bot=bot,
            session_maker=async_session_maker,
            admin_id=admin_id,
            text=text,
        )
    )
    _background_tasks.add(broadcast_task)
    broadcast_task.add_done_callback(_background_tasks.discard)

    return {
        "ok": True,
        "sent": 0,
        "blocked": 0,
        "errors": 0,
        "total": total_recipients,
        "status": "started",
    }


class AddCategoryPayload(BaseModel):
    category: str = Field(..., min_length=1, max_length=60)


class EditCategoryPayload(BaseModel):
    old_category: str = Field(..., min_length=1, max_length=60)
    new_category: str = Field(..., min_length=1, max_length=60)


class DeleteCategoryPayload(BaseModel):
    category: str = Field(..., min_length=1, max_length=60)


class AddWeaponPayload(BaseModel):
    category: str = Field(..., min_length=1, max_length=60)
    weapon: str = Field(..., min_length=1, max_length=60)


class EditWeaponPayload(BaseModel):
    category: str = Field(..., min_length=1, max_length=60)
    old_weapon: str = Field(..., min_length=1, max_length=60)
    new_weapon: str = Field(..., min_length=1, max_length=60)


class DeleteWeaponPayload(BaseModel):
    category: str = Field(..., min_length=1, max_length=60)
    weapon: str = Field(..., min_length=1, max_length=60)


class AddCatalogSkinPayload(BaseModel):
    category: str = Field(..., min_length=1, max_length=60)
    weapon: str = Field(..., min_length=1, max_length=60)
    skin: str = Field(..., min_length=1, max_length=100)


class EditSkinPayload(BaseModel):
    category: str = Field(..., min_length=1, max_length=60)
    weapon: str = Field(..., min_length=1, max_length=60)
    old_skin: str = Field(..., min_length=1, max_length=100)
    new_skin: str = Field(..., min_length=1, max_length=100)


class DeleteCatalogSkinPayload(BaseModel):
    category: str = Field(..., min_length=1, max_length=60)
    weapon: str = Field(..., min_length=1, max_length=60)
    skin: str = Field(..., min_length=1, max_length=100)


class UpdateSettingsPayload(BaseModel):
    max_active_ads: int = Field(..., ge=1, le=100)
    stars_turbo_price: int = Field(..., ge=1, le=10000)
    stars_standard_price: int = Field(..., ge=1, le=10000)
    autopost_channel_id: str | None = Field(default=None)
    autopost_enabled: bool | None = Field(default=None)
    referrals_for_top: int = Field(default=3, ge=1, le=50)


@app.post("/admin/api/catalog/category/add")
async def api_add_category(
    payload: AddCategoryPayload,
    auth_admin: bool = Depends(require_admin),
) -> Any:
    """Add a new category to the catalog."""
    _ = auth_admin
    success = add_category(payload.category)
    if not success:
        raise HTTPException(status_code=400, detail="Не удалось добавить категорию")
    logger.info("Admin added category: %s", payload.category)
    return {"ok": True}


@app.post("/admin/api/catalog/category/edit")
async def api_edit_category(
    payload: EditCategoryPayload,
    auth_admin: bool = Depends(require_admin),
) -> Any:
    """Rename a category in catalog and sync ads."""
    _ = auth_admin
    success = edit_category(payload.old_category, payload.new_category)
    if not success:
        raise HTTPException(status_code=400, detail="Не удалось переименовать категорию")

    try:
        async with async_session_maker() as session:
            await session.execute(
                update(Ad)
                .where(Ad.category == payload.old_category.strip())
                .values(category=payload.new_category.strip())
            )
            await session.commit()
    except Exception as exc:
        logger.warning("DB sync failed on category rename: %s", exc)

    logger.info("Admin renamed category: %s -> %s", payload.old_category, payload.new_category)
    return {"ok": True}


@app.post("/admin/api/catalog/category/delete")
async def api_delete_category(
    payload: DeleteCategoryPayload,
    auth_admin: bool = Depends(require_admin),
) -> Any:
    """Delete a category and its weapons from catalog."""
    _ = auth_admin
    success = delete_category(payload.category)
    if not success:
        raise HTTPException(status_code=400, detail="Не удалось удалить категорию")
    logger.info("Admin deleted category: %s", payload.category)
    return {"ok": True}


@app.post("/admin/api/catalog/weapon/add")
async def api_add_weapon(
    payload: AddWeaponPayload,
    auth_admin: bool = Depends(require_admin),
) -> Any:
    """Add a new weapon model to a category."""
    _ = auth_admin
    success = add_weapon(payload.category, payload.weapon)
    if not success:
        raise HTTPException(status_code=400, detail="Не удалось добавить модель оружия")
    logger.info("Admin added weapon model: [%s] %s", payload.category, payload.weapon)
    return {"ok": True}


@app.post("/admin/api/catalog/weapon/edit")
async def api_edit_weapon(
    payload: EditWeaponPayload,
    auth_admin: bool = Depends(require_admin),
) -> Any:
    """Rename a weapon model in catalog and sync ads."""
    _ = auth_admin
    success = edit_weapon(payload.category, payload.old_weapon, payload.new_weapon)
    if not success:
        raise HTTPException(status_code=400, detail="Не удалось переименовать модель оружия")

    try:
        async with async_session_maker() as session:
            await session.execute(
                update(Ad)
                .where(
                    Ad.category == payload.category.strip(),
                    Ad.weapon_model == payload.old_weapon.strip(),
                )
                .values(weapon_model=payload.new_weapon.strip())
            )
            await session.commit()
    except Exception as exc:
        logger.warning("DB sync failed on weapon rename: %s", exc)

    logger.info(
        "Admin renamed weapon model: [%s] %s -> %s",
        payload.category,
        payload.old_weapon,
        payload.new_weapon,
    )
    return {"ok": True}


@app.post("/admin/api/catalog/weapon/delete")
async def api_delete_weapon(
    payload: DeleteWeaponPayload,
    auth_admin: bool = Depends(require_admin),
) -> Any:
    """Delete a weapon model and its skins from catalog."""
    _ = auth_admin
    success = delete_weapon(payload.category, payload.weapon)
    if not success:
        raise HTTPException(status_code=400, detail="Не удалось удалить модель оружия")
    logger.info("Admin deleted weapon model: [%s] %s", payload.category, payload.weapon)
    return {"ok": True}


@app.post("/admin/api/catalog/add")
async def api_add_catalog_skin(
    payload: AddCatalogSkinPayload,
    auth_admin: bool = Depends(require_admin),
) -> Any:
    """Add a new skin (or comma-separated skins cleanly) to the marketplace catalog."""
    _ = auth_admin
    success = add_skin(
        category=payload.category,
        weapon=payload.weapon,
        skin=payload.skin,
    )
    if not success:
        raise HTTPException(status_code=400, detail="Некорректные параметры скина")
    logger.info(
        "Admin added skin to catalog: [%s] %s -> %s",
        payload.category,
        payload.weapon,
        payload.skin,
    )
    return {"ok": True}


@app.post("/admin/api/catalog/edit")
async def api_edit_catalog_skin(
    payload: EditSkinPayload,
    auth_admin: bool = Depends(require_admin),
) -> Any:
    """Rename a skin in the catalog and sync ads."""
    _ = auth_admin
    success = edit_skin(
        category=payload.category,
        weapon=payload.weapon,
        old_skin=payload.old_skin,
        new_skin=payload.new_skin,
    )
    if not success:
        raise HTTPException(status_code=400, detail="Не удалось переименовать скин")

    try:
        async with async_session_maker() as session:
            await session.execute(
                update(Ad)
                .where(
                    Ad.category == payload.category.strip(),
                    Ad.weapon_model == payload.weapon.strip(),
                    Ad.skin_name == payload.old_skin.strip(),
                )
                .values(skin_name=payload.new_skin.strip())
            )
            await session.commit()
    except Exception as exc:
        logger.warning("DB sync failed on skin rename: %s", exc)

    logger.info(
        "Admin renamed skin in catalog: [%s] %s | %s -> %s",
        payload.category,
        payload.weapon,
        payload.old_skin,
        payload.new_skin,
    )
    return {"ok": True}


@app.post("/admin/api/catalog/delete")
async def api_delete_catalog_skin(
    payload: DeleteCatalogSkinPayload,
    auth_admin: bool = Depends(require_admin),
) -> Any:
    """Delete a skin from the catalog."""
    _ = auth_admin
    success = delete_skin(
        category=payload.category,
        weapon=payload.weapon,
        skin=payload.skin,
    )
    if not success:
        raise HTTPException(status_code=400, detail="Не удалось удалить скин")
    logger.info(
        "Admin removed skin from catalog: [%s] %s -> %s",
        payload.category,
        payload.weapon,
        payload.skin,
    )
    return {"ok": True}


@app.post("/admin/api/catalog/reset")
async def api_reset_catalog(
    auth_admin: bool = Depends(require_admin),
) -> Any:
    """Reset catalog to factory defaults."""
    _ = auth_admin
    success = reset_catalog_to_default()
    if not success:
        raise HTTPException(status_code=500, detail="Не удалось сбросить каталог")
    logger.info("Admin reset catalog to factory defaults")
    return {"ok": True}


@app.post("/admin/api/settings/autopost/test")
async def api_test_autopost(
    auth_admin: bool = Depends(require_admin),
) -> Any:
    """Send test in-bot notification to all administrator chats."""
    _ = auth_admin
    bot = get_bot_instance()
    if not bot:
        raise HTTPException(status_code=503, detail="Telegram бот не настроен")
    if not settings.ADMIN_IDS:
        raise HTTPException(
            status_code=400,
            detail="Не найден ни один ID администратора в ADMIN_IDS (.env)",
        )

    test_text = (
        "🔔 <b>Тестовое автооповещение | Standsticker</b>\n\n"
        "Система автопостинга в боте работает корректно!\n"
        "При создании нового лота продавцом такое сообщение автоматически присылается пользователям бота в личные сообщения."
    )

    sent_count = 0
    errors: list[str] = []

    for admin_id in settings.ADMIN_IDS:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=test_text,
                parse_mode="HTML",
            )
            sent_count += 1
        except TelegramAPIError as exc:
            logger.warning("Failed sending test autopost to admin %d: %s", admin_id, exc)
            errors.append(f"{admin_id}: {exc}")

    if sent_count == 0 and errors:
        raise HTTPException(
            status_code=400,
            detail=f"Не удалось отправить тестовое сообщение администраторам: {', '.join(errors)}",
        )

    return {
        "ok": True,
        "message": f"Тестовое оповещение успешно отправлено всем администраторам ({sent_count} из {len(settings.ADMIN_IDS)})!",
    }


@app.post("/admin/api/settings")
async def api_update_settings(
    payload: UpdateSettingsPayload,
    auth_admin: bool = Depends(require_admin),
) -> Any:
    """Update dynamic marketplace settings."""
    _ = auth_admin
    updated = update_market_settings(
        max_active_ads=payload.max_active_ads,
        stars_turbo_price=payload.stars_turbo_price,
        stars_standard_price=payload.stars_standard_price,
        autopost_channel_id=payload.autopost_channel_id,
        autopost_enabled=payload.autopost_enabled,
        referrals_for_top=payload.referrals_for_top,
    )
    logger.info("Admin updated marketplace settings: %s", updated)
    return {"ok": True, "settings": updated}


@app.get("/admin/api/referrals/stats")
async def api_referrals_stats(
    auth_admin: bool = Depends(require_admin),
) -> Any:
    """Return platform-wide referral analytics, metrics, and top referrers."""
    _ = auth_admin
    async with async_session_maker() as session:
        ref_repo = ReferralRepository(session)
        stats = await ref_repo.get_system_referral_stats()
        top_referrers = await ref_repo.get_top_referrers(limit=10)

    return {
        "ok": True,
        "stats": stats,
        "top_referrers": top_referrers,
    }


@app.get("/admin/api/ads/realtime-check")
async def api_ads_realtime_check(
    last_known_id: int = 0,
    filter: str = "active",
    seller_id: int | None = None,
    auth_admin: bool = Depends(require_admin),
) -> Any:
    """Check for newly created marketplace ads in real-time without page reload."""
    _ = auth_admin
    async with async_session_maker() as session:
        repo = AdminRepository(session)
        result = await repo.get_ads_realtime_status(
            filter_type=filter,
            seller_id=seller_id,
            last_known_id=last_known_id,
        )

    serialized_ads: list[dict[str, Any]] = []
    for ad in result["new_ads"]:
        sticker_text = (
            "Без наклеек"
            if ad.sticker_count == 0
            else f"{ad.sticker_count} шт. • {ad.sticker_rarity}"
        )
        serialized_ads.append(
            {
                "id": ad.id,
                "weapon_model": ad.weapon_model,
                "skin_name": ad.skin_name,
                "category": ad.category,
                "sticker_count": ad.sticker_count,
                "sticker_rarity": ad.sticker_rarity or "—",
                "sticker_label": sticker_text,
                "price_gold": ad.price_gold,
                "seller_id": ad.seller_id,
                "is_active": ad.is_active,
                "is_top": ad.is_top,
                "views_count": ad.views_count,
                "clicks_count": ad.clicks_count,
                "created_at": ad.created_at.strftime("%d.%m.%Y") if ad.created_at else "—",
            }
        )

    return {
        "latest_ad_id": result["latest_ad_id"],
        "total_count": result["total_count"],
        "has_new": result["has_new"],
        "new_count": len(serialized_ads),
        "new_ads": serialized_ads,
    }


@app.get("/admin/api/dashboard/realtime")
async def api_dashboard_realtime(
    auth_admin: bool = Depends(require_admin),
) -> Any:
    """Provide real-time statistics poll for the admin dashboard."""
    _ = auth_admin
    async with async_session_maker() as session:
        repo = AdminRepository(session)
        stats = await repo.get_system_stats()
        latest_ad_stmt = select(func.coalesce(func.max(Ad.id), 0)).where(Ad.is_active.is_(True))
        latest_ad_id = int((await session.execute(latest_ad_stmt)).scalar() or 0)

    return {
        "stats": stats,
        "latest_ad_id": latest_ad_id,
    }




def run() -> None:
    """Run Web Admin Panel server."""
    setup_app_logging(logging.INFO)
    logger.info(
        "Starting Standsticker Admin Hub at http://%s:%d/admin",
        settings.WEB_ADMIN_HOST,
        settings.WEB_ADMIN_PORT,
    )
    uvicorn.run(
        "src.web.app:app",
        host=settings.WEB_ADMIN_HOST,
        port=settings.WEB_ADMIN_PORT,
        reload=False,
    )


if __name__ == "__main__":
    run()
