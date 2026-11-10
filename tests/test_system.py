import asyncio
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient

from src.config import settings
from src.data.game_catalog import (
    STICKER_RARITIES,
    get_categories,
    get_skins,
    get_weapons,
    is_valid_selection,
    is_valid_sticker_rarity,
)
from src.services.notification import PUSH_DELAY_SECONDS
from src.services.scheduler import setup_scheduler
from src.utils.formatters import format_ad_caption, format_sticker_label
from src.web.app import (
    app,
    create_admin_session_token,
    verify_admin_session_token,
)


class TestGameCatalog:
    """Validate game catalog consistency and selection logic."""

    def test_catalog_structure(self) -> None:
        categories = get_categories()
        assert len(categories) > 0
        assert "Пистолеты" in categories
        assert "Винтовки" in categories
        assert "Снайперские винтовки" in categories

    def test_weapons_and_skins(self) -> None:
        weapons = get_weapons("Пистолеты")
        assert "Glock" in weapons or "USP" in weapons

        first_weapon = weapons[0]
        skins = get_skins("Пистолеты", first_weapon)
        assert len(skins) > 0

    def test_is_valid_selection(self) -> None:
        assert is_valid_selection("Пистолеты", "USP", "Genesis") is True
        assert is_valid_selection("Пистолеты", "AKR", "Genesis") is False
        assert is_valid_selection("Винтовки", "AKR", "NonExistentSkinXYZ") is False

    def test_sticker_rarities(self) -> None:
        assert len(STICKER_RARITIES) > 0
        for rarity in STICKER_RARITIES:
            assert is_valid_sticker_rarity(rarity) is True
        assert is_valid_sticker_rarity("NonExistentRarity") is False


class TestFormatters:
    """Validate Markdown formatting and caption generations."""

    def test_sticker_label_formatting(self) -> None:
        assert format_sticker_label(0, None, bold=False) == "Без наклеек"
        assert format_sticker_label(0, None, bold=True) == "<b>Без наклеек</b>"
        assert format_sticker_label(4, "Arcane", bold=False) == "4 шт. (Arcane)"
        assert format_sticker_label(4, "Arcane", bold=True) == "<b>4 шт. (Arcane)</b>"

    def test_ad_caption_no_stickers(self) -> None:
        class DummyAd:
            id = 42
            weapon_model = "M4"
            skin_name = "Necromancer"
            sticker_count = 0
            sticker_rarity = None
            price_gold = 1500
            is_top = False
            views_count = 10
            seller_id = 999
            seller = None
            created_at = datetime.now(UTC)

        caption = format_ad_caption(DummyAd(), mode="buyer")  # type: ignore[arg-type]
        assert "M4 | Necromancer" in caption
        assert "Без наклеек" in caption
        assert "1500 G" in caption

    def test_ad_caption_with_stickers(self) -> None:
        class DummyAd:
            id = 43
            weapon_model = "AKR"
            skin_name = "Treasure Hunter"
            sticker_count = 4
            sticker_rarity = "Arcane"
            price_gold = 50000
            is_top = True
            views_count = 120
            seller_id = 999
            seller = None
            created_at = datetime.now(UTC)

        caption = format_ad_caption(DummyAd(), mode="buyer")  # type: ignore[arg-type]
        assert "AKR | Treasure Hunter" in caption
        assert "4 шт." in caption
        assert "Arcane" in caption
        assert "50000 G" in caption


class TestWebSecurityAndAuth:
    """Validate web auth HMAC signatures and security barriers."""

    def test_admin_cookie_valid(self) -> None:
        token = create_admin_session_token()
        assert verify_admin_session_token(token) is True

    def test_admin_cookie_tampered(self) -> None:
        token = create_admin_session_token()
        payload_part, _sig = token.split(".", 1)
        tampered = f"{payload_part}.00000000000000000000000000000000"
        assert verify_admin_session_token(tampered) is False

    def test_admin_cookie_malformed(self) -> None:
        assert verify_admin_session_token("malformed") is False
        assert verify_admin_session_token("") is False
        assert verify_admin_session_token(None) is False

    def test_admin_cookie_expired(self) -> None:
        expired_token = create_admin_session_token(expires_in=-10)
        assert verify_admin_session_token(expired_token) is False


class TestFastApiEndpoints:
    """Validate FastAPI routes, /health, parameter checks and authorization."""

    def setup_method(self) -> None:
        self.client = TestClient(app)

    def test_health_check(self) -> None:
        response = self.client.get("/health")
        assert response.status_code in (200, 503)
        data = response.json()
        assert "status" in data or "detail" in data

    def test_admin_unauthorized_redirect(self) -> None:
        response = self.client.get("/admin/ads", follow_redirects=False)
        assert response.status_code in (302, 303, 307)
        assert "/admin/login" in response.headers.get("location", "")

    def test_admin_login_wrong_secret(self) -> None:
        response = self.client.post(
            "/admin/login",
            data={"secret": "WrongSecret123"},
            follow_redirects=False,
        )
        assert response.status_code == 401

    def test_admin_login_success(self) -> None:
        response = self.client.post(
            "/admin/login",
            data={"secret": settings.WEB_ADMIN_SECRET},
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)
        assert "admin_session" in response.cookies

    def test_path_parameter_validation_overflow(self) -> None:
        token = create_admin_session_token()
        self.client.cookies.set("admin_session", token)

        response = self.client.get("/admin/ads/999999999999999")
        assert response.status_code == 422

    def test_path_parameter_validation_negative(self) -> None:
        token = create_admin_session_token()
        self.client.cookies.set("admin_session", token)

        response = self.client.get("/admin/ads/-5")
        assert response.status_code == 422


class TestSchedulerAndNotifications:
    """Validate scheduler settings and broadcast safety constants."""

    def test_push_delay_is_protective(self) -> None:
        assert PUSH_DELAY_SECONDS >= 0.035

    def test_scheduler_initialization_defaults(self) -> None:
        class DummyBot:
            pass

        class DummySessionMaker:
            pass

        sched = setup_scheduler(DummyBot(), DummySessionMaker())  # type: ignore[arg-type]
        assert sched is not None
        jobs = sched.get_jobs()
        assert len(jobs) == 4
        job_ids = {job.id for job in jobs}
        assert "clean_expired_top_ads" in job_ids
        assert "auto_archive_stale_ads" in job_ids
        assert "auto_close_abandoned_chats" in job_ids
        assert "daily_database_backup" in job_ids


class TestDynamicMarketFeatures:
    """Validate dynamic settings, custom skin catalog, and logging service."""

    def test_dynamic_max_active_ads_default(self) -> None:
        from src.data.settings_manager import get_max_active_ads, update_market_settings
        assert get_max_active_ads() == 10

        update_market_settings(max_active_ads=15)
        assert get_max_active_ads() == 15

        update_market_settings(max_active_ads=10)
        assert get_max_active_ads() == 10

    def test_custom_catalog_lifecycle(self) -> None:
        from src.data.game_catalog import (
            add_custom_skin,
            delete_custom_skin,
            is_valid_selection,
        )

        cat, wep, skn = "СпецОружие", "LaserGun", "PlasmaBeam"
        assert is_valid_selection(cat, wep, skn) is False

        added = add_custom_skin(cat, wep, skn)
        assert added is True
        assert is_valid_selection(cat, wep, skn) is True

        deleted = delete_custom_skin(cat, wep, skn)
        assert deleted is True
        assert is_valid_selection(cat, wep, skn) is False

    def test_logging_service(self) -> None:
        from src.services.logger_service import (
            count_log_sources,
            get_log_file_path,
            list_available_log_dates,
            read_parsed_logs,
        )

        dates = list_available_log_dates()
        assert len(dates) > 0
        path = get_log_file_path()
        assert path.name.endswith(".txt")
        assert path.name.startswith("app_")

        logs = read_parsed_logs()
        assert isinstance(logs, list)

        counts = count_log_sources()
        assert "all" in counts and "bot" in counts and "web" in counts

        bot_logs = read_parsed_logs(source="bot")
        assert all(e.get("source") == "bot" for e in bot_logs)

        web_logs = read_parsed_logs(source="web")
        assert all(e.get("source") == "web" for e in web_logs)

    def test_web_admin_catalog_and_settings_endpoints(self) -> None:
        client = TestClient(app)
        token = create_admin_session_token()
        client.cookies.set("admin_session", token)

        r_catalog = client.get("/admin/catalog")
        assert r_catalog.status_code == 200
        assert "Каталог" in r_catalog.text

        r_settings = client.get("/admin/settings")
        assert r_settings.status_code == 200
        assert "Лимиты" in r_settings.text

        r_logs = client.get("/admin/logs")
        assert r_logs.status_code == 200
        assert "Telegram-бот" in r_logs.text
        assert "Веб-админка" in r_logs.text

        r_api_set = client.post(
            "/admin/api/settings",
            json={
                "max_active_ads": 10,
                "stars_turbo_price": 150,
                "stars_standard_price": 50,
            },
        )
        assert r_api_set.status_code == 200
        assert r_api_set.json()["ok"] is True

    def test_broadcast_detect_parse_mode(self) -> None:
        from src.services.broadcast import detect_parse_mode

        assert detect_parse_mode("<b>жирный</b>") == "HTML"
        assert detect_parse_mode("<a href='https://example.com'>link</a>") == "HTML"
        assert detect_parse_mode("<code>код</code>") == "HTML"
        assert detect_parse_mode("**жирный текст**") == "Markdown"
        assert detect_parse_mode("Обычный текст без тегов") == "HTML"

    def test_dynamic_catalog_and_settings_sync(self) -> None:
        from src.data.game_catalog import (
            add_custom_skin,
            delete_custom_skin,
            get_skins,
        )
        from src.data.settings_manager import (
            get_max_active_ads,
            get_stars_turbo_price,
            update_market_settings,
        )

        update_market_settings(max_active_ads=7, stars_turbo_price=120)
        assert get_max_active_ads() == 7
        assert get_stars_turbo_price() == 120

        update_market_settings(max_active_ads=10, stars_turbo_price=150)
        assert get_max_active_ads() == 10

        added = add_custom_skin("Винтовки", "AKR", "TestDynamicSkin")
        assert added is True
        skins = get_skins("Винтовки", "AKR")
        assert "TestDynamicSkin" in skins

        deleted = delete_custom_skin("Винтовки", "AKR", "TestDynamicSkin")
        assert deleted is True
        skins_after = get_skins("Винтовки", "AKR")
        assert "TestDynamicSkin" not in skins_after

    def test_catalog_crud_and_comma_split(self) -> None:
        from src.data.game_catalog import (
            add_weapon,
            edit_weapon,
            delete_weapon,
            add_skin,
            edit_skin,
            delete_skin,
            get_weapons,
            get_skins,
        )
        # Test adding weapon
        assert add_weapon("Винтовки", "TestWeaponModel") is True
        assert "TestWeaponModel" in get_weapons("Винтовки")

        # Test renaming weapon
        assert edit_weapon("Винтовки", "TestWeaponModel", "TestWeaponModelRenamed") is True
        assert "TestWeaponModelRenamed" in get_weapons("Винтовки")
        assert "TestWeaponModel" not in get_weapons("Винтовки")

        # Test adding multiple skins via comma-separated string
        assert add_skin("Винтовки", "TestWeaponModelRenamed", "SkinOne, SkinTwo, SkinThree") is True
        w_skins = get_skins("Винтовки", "TestWeaponModelRenamed")
        assert "SkinOne" in w_skins
        assert "SkinTwo" in w_skins
        assert "SkinThree" in w_skins
        assert "SkinOne, SkinTwo, SkinThree" not in w_skins

        # Test editing skin
        assert edit_skin("Винтовки", "TestWeaponModelRenamed", "SkinOne", "SkinOneRenamed") is True
        w_skins = get_skins("Винтовки", "TestWeaponModelRenamed")
        assert "SkinOneRenamed" in w_skins
        assert "SkinOne" not in w_skins

        # Test deleting skin
        assert delete_skin("Винтовки", "TestWeaponModelRenamed", "SkinOneRenamed") is True
        assert "SkinOneRenamed" not in get_skins("Винтовки", "TestWeaponModelRenamed")

        # Clean up weapon
        assert delete_weapon("Винтовки", "TestWeaponModelRenamed") is True
        assert "TestWeaponModelRenamed" not in get_weapons("Винтовки")

    def test_keyboard_callback_data_length_limit(self) -> None:
        from src.data.game_catalog import add_skin, delete_skin
        from src.keyboards.seller import get_skins_keyboard
        from src.keyboards.buyer import get_filter_skins_keyboard

        long_skin_name = "ОченьДлинноеНазваниеСкинаКотороеРаньшеЛомалоТелеграм"
        add_skin("Винтовки", "AKR", long_skin_name)

        # Seller keyboard
        kb_seller = get_skins_keyboard("Винтовки", "AKR")
        for row in kb_seller.inline_keyboard:
            for btn in row:
                assert len(btn.callback_data.encode("utf-8")) <= 64, f"Button {btn.callback_data} exceeds 64 bytes"

        # Buyer filter keyboard
        kb_buyer = get_filter_skins_keyboard("Винтовки", "AKR")
        for row in kb_buyer.inline_keyboard:
            for btn in row:
                assert len(btn.callback_data.encode("utf-8")) <= 64, f"Button {btn.callback_data} exceeds 64 bytes"

        delete_skin("Винтовки", "AKR", long_skin_name)

    @pytest.mark.asyncio
    async def test_database_backup_24h_retention(self) -> None:
        import os
        from src.services.backup_service import perform_database_backup, BACKUP_DIR

        dummy_db = "test_backup_dummy.db"
        with open(dummy_db, "w", encoding="utf-8") as f:
            f.write("TEST_DB_PAYLOAD")

        orig_url = settings.DATABASE_URL
        settings.DATABASE_URL = f"sqlite+aiosqlite:///{dummy_db}"

        try:
            # First backup execution
            success1 = await perform_database_backup()
            assert success1 is True
            files1 = list(BACKUP_DIR.glob("*"))
            assert len(files1) == 1
            assert files1[0].name.startswith("standoff_market_backup_latest")

            # Second backup execution (24h simulation: replaces previous backup)
            success2 = await perform_database_backup()
            assert success2 is True
            files2 = list(BACKUP_DIR.glob("*"))
            assert len(files2) == 1
            assert files2[0].name.startswith("standoff_market_backup_latest")
        finally:
            settings.DATABASE_URL = orig_url
            if os.path.exists(dummy_db):
                os.remove(dummy_db)
            for f in BACKUP_DIR.glob("*"):
                f.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_safe_send_photo_fallback(self) -> None:
        from aiogram.exceptions import TelegramBadRequest
        from src.utils.helpers import safe_send_photo

        class MockBotWrongIdentifier:
            def __init__(self) -> None:
                self.sent_messages: list[dict[str, Any]] = []

            async def send_photo(self, **kwargs: Any) -> Any:
                raise TelegramBadRequest(
                    method=kwargs.get("method", "sendPhoto"),  # type: ignore[arg-type]
                    message="Telegram server says - Bad Request: wrong file identifier/HTTP URL specified",
                )

            async def send_message(self, **kwargs: Any) -> Any:
                self.sent_messages.append(kwargs)
                return kwargs

        mock_bot = MockBotWrongIdentifier()
        result = await safe_send_photo(
            bot=mock_bot,  # type: ignore[arg-type]
            chat_id=123456,
            photo="wrong_old_bot_file_id",
            caption="Test Lot Caption",
        )
        assert result is not None
        assert len(mock_bot.sent_messages) == 1
        assert "Test Lot Caption" in mock_bot.sent_messages[0]["text"]
        assert "📷 <b>[Фото лота недоступно]</b>" in mock_bot.sent_messages[0]["text"]


class TestAutopostAndRealtime:
    """Validate real-time ads monitoring and Telegram channel autoposting."""

    def test_autopost_caption_and_keyboard(self) -> None:
        from src.services.autopost import build_inbot_autopost_caption, build_inbot_autopost_keyboard

        class DummyAd:
            id = 777
            weapon_model = "AWM"
            skin_name = "Genesis"
            category = "Снайперские винтовки"
            sticker_count = 4
            sticker_rarity = "Arcane"
            price_gold = 25000
            seller_id = 987654
            is_top = True

        caption = build_inbot_autopost_caption(DummyAd())  # type: ignore[arg-type]
        assert "AWM | Genesis" in caption
        assert "25 000 G" in caption
        assert "ТОП-ПРЕДЛОЖЕНИЕ" in caption
        assert "👤 <b>Продавец:</b>" in caption
        assert "ID `987654`" not in caption

        kb = build_inbot_autopost_keyboard(ad_id=777)
        assert len(kb.inline_keyboard) == 1
        button = kb.inline_keyboard[0][0]
        assert button.callback_data == "chat_start:777"
        assert "Написать продавцу" in button.text

    @pytest.mark.asyncio
    async def test_admin_repo_realtime_status_logic(self) -> None:
        from src.database.repositories.admin_repo import AdminRepository

        class MockRow:
            max_id = 42
            total_count = 10

        class MockResult:
            def one(self) -> Any:
                return MockRow()

            def scalars(self) -> Any:
                class MockScalars:
                    def all(self) -> list[Any]:
                        return []
                return MockScalars()

        class MockSession:
            async def execute(self, stmt: Any) -> Any:
                return MockResult()

        repo = AdminRepository(MockSession())  # type: ignore[arg-type]
        status = await repo.get_ads_realtime_status(last_known_id=40)
        assert status["latest_ad_id"] == 42
        assert status["total_count"] == 10
        assert status["has_new"] is True

        status_no_new = await repo.get_ads_realtime_status(last_known_id=42)
        assert status_no_new["has_new"] is False

    def test_realtime_api_requires_auth(self) -> None:
        client = TestClient(app)
        res = client.get("/admin/api/ads/realtime-check")
        assert res.status_code == 401

        dash_res = client.get("/admin/api/dashboard/realtime")
        assert dash_res.status_code == 401

    def test_settings_autopost_persistence(self) -> None:
        from src.data.settings_manager import (
            get_autopost_channel_id,
            is_autopost_enabled,
            update_market_settings,
        )

        update_market_settings(
            autopost_channel_id="@test_standsticker_feed",
            autopost_enabled=True,
        )
        assert get_autopost_channel_id() == "@test_standsticker_feed"
        assert is_autopost_enabled() is True

        update_market_settings(
            autopost_channel_id="",
            autopost_enabled=False,
        )
        assert is_autopost_enabled() is False

        # Restore enabled state
        update_market_settings(autopost_enabled=True)
        assert is_autopost_enabled() is True

    @pytest.mark.asyncio
    async def test_channel_autopost_flow(self) -> None:
        from unittest.mock import AsyncMock, MagicMock, patch
        from src.database.models import Ad
        from src.services.autopost import (
            _execute_channel_autopost,
            autopost_new_ad,
            build_channel_autopost_keyboard,
            build_inbot_ad_keyboard,
            build_inbot_autopost_caption,
            build_inbot_autopost_keyboard,
        )

        ad = Ad(
            id=99,
            seller_id=12345,
            weapon_model="AKR",
            skin_name="Treasure",
            category="Винтовки",
            sticker_count=4,
            sticker_rarity="Rare",
            price_gold=750,
            photo_file_id="photo_token_123",
            is_top=True,
        )

        caption = build_inbot_autopost_caption(ad, seller_name="Артур")
        assert "AKR | Treasure" in caption
        assert "750 G" in caption
        assert "ТОП-ПРЕДЛОЖЕНИЕ" in caption
        assert "👤 <b>Продавец:</b> Артур" in caption
        assert "ID" not in caption.split("👤 <b>Продавец:</b>")[1]

        kb = build_inbot_autopost_keyboard(99)
        assert kb.inline_keyboard[0][0].callback_data == "chat_start:99"
        assert build_inbot_ad_keyboard(99).inline_keyboard[0][0].callback_data == "chat_start:99"

        channel_kb = build_channel_autopost_keyboard("TestBot", 99)
        assert channel_kb.inline_keyboard[0][0].url == "https://t.me/TestBot?start=ad_99"

        mock_bot = MagicMock()
        mock_bot.get_me = AsyncMock(return_value=MagicMock(username="TestBot"))
        mock_safe_send = AsyncMock(return_value=MagicMock())

        with patch("src.services.autopost.safe_send_photo", mock_safe_send):
            success = await _execute_channel_autopost(
                bot=mock_bot,
                ad_id=99,
                photo_file_id="photo_token_123",
                caption=caption,
                channel_id="@standsticker_channel",
            )
            assert success is True
            assert mock_safe_send.call_count == 1

    @pytest.mark.asyncio
    async def test_inbot_autopost_delivers_without_channel(self) -> None:
        """New ad autopost triggers in-bot delivery even when channel_id is not configured."""
        from src.database.models import Ad
        from src.services.autopost import autopost_new_ad

        ad = Ad(
            id=101,
            seller_id=5555,
            weapon_model="M4",
            skin_name="Samurai",
            category="Винтовки",
            sticker_count=0,
            sticker_rarity="—",
            price_gold=1200,
            photo_file_id="photo_token_456",
            is_top=False,
        )

        mock_bot = MagicMock()
        mock_safe_send = AsyncMock(return_value=MagicMock())

        with patch("src.services.autopost.is_autopost_enabled", return_value=True), \
             patch("src.services.autopost.get_autopost_channel_id", return_value=""), \
             patch("src.services.autopost.safe_send_photo", mock_safe_send), \
             patch("src.services.autopost.AdminRepository.get_all_active_user_ids", AsyncMock(return_value=[111, 222, 5555])):
            result = await autopost_new_ad(
                bot=mock_bot,
                ad=ad,
                skip_user_ids={111},
                seller_name="Seller",
            )
            assert result is True
            # Let background task run
            await asyncio.sleep(0.05)
            # 5555 is seller (skipped), 111 is in skip_user_ids (skipped) -> only 222 notified
            assert mock_safe_send.call_count == 1
            call_kwargs = mock_safe_send.call_args[1]
            assert call_kwargs["chat_id"] == 222

    @pytest.mark.asyncio
    async def test_api_test_autopost_all_admins(self) -> None:
        """Test autopost endpoint sends notifications to all configured admins."""
        from src.web.app import api_test_autopost
        from src.config import settings

        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock(return_value=MagicMock())

        with patch("src.web.app.get_bot_instance", return_value=mock_bot), \
             patch.object(settings, "ADMIN_IDS", [1001, 1002, 1003]):
            res = await api_test_autopost(auth_admin=True)
            assert res["ok"] is True
            assert "3 из 3" in res["message"]
            assert mock_bot.send_message.call_count == 3
            sent_chats = [c[1]["chat_id"] for c in mock_bot.send_message.call_args_list]
            assert sent_chats == [1001, 1002, 1003]






