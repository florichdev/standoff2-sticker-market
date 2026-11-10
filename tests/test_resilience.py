"""Automated test suite for resilience, security rate limiting, and chat message relay robustness."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, Message, PhotoSize, User as TgUser
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.testclient import TestClient

from src.config import settings
from src.database.base import engine
from src.database.models import Ad, Base, ChatSession, User
from src.database.repositories.chat_repo import ChatRepository
from src.handlers.chat import ChatStates, relay_chat_photo, relay_chat_text
from src.main import create_storage
from src.web.app import (
    _login_failed_attempts,
    app,
    check_login_rate_limit,
    record_failed_login,
    reset_failed_logins,
)


@asynccontextmanager
async def create_isolated_chat_session():
    """Create an isolated SQLite database session for chat resilience tests."""
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        test_engine, expire_on_commit=False, class_=AsyncSession
    )
    async with session_factory() as session:
        yield session

    await test_engine.dispose()


class TestDatabaseAndFSMResilience:
    """Validate connection pool hardening and FSM storage fallback mechanism."""

    def test_database_engine_resilience_config(self) -> None:
        """Verify engine is configured with pool_pre_ping and custom pool parameters."""
        assert engine.pool._pre_ping is True
        assert settings.DB_POOL_SIZE == 20
        assert settings.DB_MAX_OVERFLOW == 10
        assert settings.DB_POOL_RECYCLE == 1800

    def test_create_storage_defaults_to_memory(self) -> None:
        """Verify MemoryStorage is selected when REDIS_URL is not configured."""
        with patch.object(settings, "REDIS_URL", None):
            storage = create_storage()
            assert isinstance(storage, MemoryStorage)

    def test_create_storage_graceful_fallback_on_redis_error(self) -> None:
        """Verify graceful fallback to MemoryStorage if Redis connection fails or driver is absent."""
        with patch.object(settings, "REDIS_URL", "redis://nonexistent-redis-host:6379/0"):
            storage = create_storage()
            assert isinstance(storage, MemoryStorage)


class TestAdminBruteForceRateLimiter:
    """Validate brute force protection and rate limiter for admin panel authentication."""

    def setup_method(self) -> None:
        _login_failed_attempts.clear()

    def teardown_method(self) -> None:
        _login_failed_attempts.clear()

    def test_login_rate_limiting_helper_window(self) -> None:
        """Check internal rate limiting helper allows up to 5 attempts and blocks the 6th."""
        test_ip = "192.168.1.100"
        for _ in range(5):
            allowed, _ = check_login_rate_limit(test_ip)
            assert allowed is True
            record_failed_login(test_ip)

        blocked, retry_after = check_login_rate_limit(test_ip)
        assert blocked is False
        assert retry_after > 0

        reset_failed_logins(test_ip)
        allowed_again, _ = check_login_rate_limit(test_ip)
        assert allowed_again is True

    def test_admin_login_endpoint_blocks_brute_force(self) -> None:
        """Verify POST /admin/login returns 401 for bad secrets and 429 on exceeding threshold."""
        client = TestClient(app)
        test_ip = "203.0.113.199"

        # First 5 attempts fail with 401 Unauthorized
        for i in range(5):
            resp = client.post(
                "/admin/login",
                data={"secret": f"wrong-pass-{i}"},
                headers={"X-Forwarded-For": test_ip},
            )
            assert resp.status_code == 401

        # 6th attempt should be blocked with 429 Too Many Requests
        resp = client.post(
            "/admin/login",
            data={"secret": "wrong-pass-6"},
            headers={"X-Forwarded-For": test_ip},
        )
        assert resp.status_code == 429
        assert "Retry-After" in resp.headers
        assert "Слишком много неудачных попыток" in resp.text


class TestAnonymousChatResilience:
    """Validate chat message relay against entity parsing errors and media caption overflow."""

    @pytest.mark.asyncio
    async def test_relay_chat_text_escapes_special_characters(self) -> None:
        """Verify text containing markdown/HTML characters is safely escaped and relayed."""
        async with create_isolated_chat_session() as session:
            buyer = User(id=111, username="buyer1")
            seller = User(id=222, username="seller1")
            ad = Ad(
                id=1,
                seller_id=222,
                category="Rifle",
                weapon_model="AKR",
                skin_name="Dragon",
                price_gold=150,
                photo_file_id="photo123",
                is_active=True,
                sticker_count=0,
                sticker_rarity="",
            )
            chat_session = ChatSession(id=1, buyer_id=111, seller_id=222, is_active=True, ad_id=1)
            session.add_all([buyer, seller, ad, chat_session])
            await session.flush()

            bot_mock = AsyncMock()
            bot_mock.id = 999
            bot_mock.send_message = AsyncMock()

            storage = MemoryStorage()
            state = FSMContext(
                storage=storage,
                key=StorageKey(bot_id=999, chat_id=111, user_id=111),
            )
            await state.set_state(ChatStates.in_chat)
            await state.update_data(active_session_id=1)

            message = MagicMock(spec=Message)
            message.bot = bot_mock
            message.from_user = TgUser(id=111, is_bot=False, first_name="Buyer")
            message.text = "Hello! *bold* _italic_ [bracket] <script>alert(1)</script>"

            await relay_chat_text(
                message=message,
                user=buyer,
                session=session,
                state=state,
            )

            assert bot_mock.send_message.called
            call_kwargs = bot_mock.send_message.call_args.kwargs
            assert call_kwargs["chat_id"] == 222
            assert call_kwargs["parse_mode"] == ParseMode.HTML
            # Special tags must be escaped
            assert "&lt;script&gt;" in call_kwargs["text"]

    @pytest.mark.asyncio
    async def test_relay_chat_text_parsing_fallback(self) -> None:
        """Verify fallback to parse_mode=None when Telegram API rejects entity formatting."""
        async with create_isolated_chat_session() as session:
            buyer = User(id=333, username="buyer2")
            seller = User(id=444, username="seller2")
            chat_session = ChatSession(id=2, buyer_id=333, seller_id=444, is_active=True)
            session.add_all([buyer, seller, chat_session])
            await session.flush()

            bot_mock = AsyncMock()
            bot_mock.id = 999

            # First call raises entity parsing error, second succeeds
            bad_request = TelegramBadRequest(
                method=MagicMock(),
                message="Bad Request: can't parse entities in message text",
            )
            bot_mock.send_message = AsyncMock(side_effect=[bad_request, None])

            storage = MemoryStorage()
            state = FSMContext(
                storage=storage,
                key=StorageKey(bot_id=999, chat_id=333, user_id=333),
            )
            await state.set_state(ChatStates.in_chat)
            await state.update_data(active_session_id=2)

            message = MagicMock(spec=Message)
            message.bot = bot_mock
            message.from_user = TgUser(id=333, is_bot=False, first_name="Buyer2")
            message.text = "Corrupted entities test"

            await relay_chat_text(
                message=message,
                user=buyer,
                session=session,
                state=state,
            )

            assert bot_mock.send_message.call_count == 2
            fallback_call_kwargs = bot_mock.send_message.call_args_list[1].kwargs
            assert fallback_call_kwargs["parse_mode"] is None
            assert "Corrupted entities test" in fallback_call_kwargs["text"]

    @pytest.mark.asyncio
    async def test_relay_chat_photo_caption_splitting(self) -> None:
        """Verify captions exceeding Telegram's 1024-character limit are split into photo + text message."""
        async with create_isolated_chat_session() as session:
            buyer = User(id=555, username="buyer3")
            seller = User(id=666, username="seller3")
            chat_session = ChatSession(id=3, buyer_id=555, seller_id=666, is_active=True)
            session.add_all([buyer, seller, chat_session])
            await session.flush()

            bot_mock = AsyncMock()
            bot_mock.id = 999
            bot_mock.send_photo = AsyncMock()
            bot_mock.send_message = AsyncMock()

            storage = MemoryStorage()
            state = FSMContext(
                storage=storage,
                key=StorageKey(bot_id=999, chat_id=555, user_id=555),
            )
            await state.set_state(ChatStates.in_chat)
            await state.update_data(active_session_id=3)

            photo_size = PhotoSize(
                file_id="photo_file_123",
                file_unique_id="unique_123",
                width=800,
                height=600,
            )

            message = MagicMock(spec=Message)
            message.bot = bot_mock
            message.photo = [photo_size]
            # Caption longer than 1024 chars
            long_caption = "X" * 1100
            message.caption = long_caption

            await relay_chat_photo(
                message=message,
                user=buyer,
                session=session,
                state=state,
            )

            # Photo should be sent WITHOUT caption
            assert bot_mock.send_photo.called
            photo_kwargs = bot_mock.send_photo.call_args.kwargs
            assert "caption" not in photo_kwargs or photo_kwargs.get("caption") is None

            # Full text should be sent via send_message
            assert bot_mock.send_message.called
            msg_kwargs = bot_mock.send_message.call_args.kwargs
            assert msg_kwargs["chat_id"] == 666
            assert long_caption in msg_kwargs["text"]

    @pytest.mark.asyncio
    async def test_relay_chat_recipient_deactivated_closes_chat(self) -> None:
        """Verify chat session is gracefully closed when recipient has deactivated account."""
        async with create_isolated_chat_session() as session:
            buyer = User(id=777, username="buyer4")
            seller = User(id=888, username="seller4")
            chat_session = ChatSession(id=4, buyer_id=777, seller_id=888, is_active=True)
            session.add_all([buyer, seller, chat_session])
            await session.flush()

            bot_mock = AsyncMock()
            bot_mock.id = 999
            bad_request = TelegramBadRequest(
                method=MagicMock(),
                message="Bad Request: user is deactivated",
            )
            bot_mock.send_message = AsyncMock(side_effect=bad_request)

            storage = MemoryStorage()
            state = FSMContext(
                storage=storage,
                key=StorageKey(bot_id=999, chat_id=777, user_id=777),
            )
            await state.set_state(ChatStates.in_chat)
            await state.update_data(active_session_id=4)

            message = MagicMock(spec=Message)
            message.bot = bot_mock
            message.from_user = TgUser(id=777, is_bot=False, first_name="Buyer4")
            message.text = "Are you there?"
            message.answer = AsyncMock()

            await relay_chat_text(
                message=message,
                user=buyer,
                session=session,
                state=state,
            )

            # Message informed user that chat was closed
            assert message.answer.called
            assert "Собеседник недоступен" in message.answer.call_args.args[0]

            # Chat state cleared
            curr_state = await state.get_state()
            assert curr_state is None

            # Chat session marked inactive in DB
            db_chat = await session.get(ChatSession, 4)
            assert db_chat is not None
            assert db_chat.is_active is False
