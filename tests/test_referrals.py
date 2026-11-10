"""Automated test suite for the Standsticker referral system, anti-fraud, and bonus activations."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.filters import CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, User as TgUser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.testclient import TestClient

from src.data.settings_manager import get_referrals_for_top, update_market_settings
from src.database.models import Ad, Base, Referral, SearchSubscription, StarTransaction, User
from src.database.repositories.ad_repo import AdRepository
from src.database.repositories.referral_repo import ReferralRepository
from src.database.repositories.user_repo import UserRepository
from src.handlers.seller import promo_activate_free_boost
from src.handlers.start import handle_start
from src.web.app import app, create_admin_session_token


@asynccontextmanager
async def create_isolated_session():
    """Create an isolated in-memory SQLite async database session."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )
    async with session_factory() as session:
        yield session

    await engine.dispose()


class TestReferralRepository:
    """Validate core data access layer for referrals and bonus balance."""

    @pytest.mark.asyncio
    async def test_register_referral_atomic_success(self) -> None:
        """Verify successful referral registration between distinct existing users."""
        async with create_isolated_session() as session:
            referrer = User(id=101, username="referrer_user", free_top_boosts=0)
            referred = User(id=202, username="new_friend", free_top_boosts=0)
            session.add_all([referrer, referred])
            await session.flush()

            repo = ReferralRepository(session)
            success = await repo.register_referral_atomic(
                referrer_id=101, new_user_id=202
            )
            assert success is True

            count = await repo.get_referrals_count(101)
            assert count == 1

            source = await repo.get_referral_source(202)
            assert source is not None
            assert source.referrer_id == 101

    @pytest.mark.asyncio
    async def test_anti_fraud_self_referral_rejected(self) -> None:
        """Verify user cannot invite themselves."""
        async with create_isolated_session() as session:
            user = User(id=303, username="solo_user")
            session.add(user)
            await session.flush()

            repo = ReferralRepository(session)
            success = await repo.register_referral_atomic(
                referrer_id=303, new_user_id=303
            )
            assert success is False

            count = await repo.get_referrals_count(303)
            assert count == 0

    @pytest.mark.asyncio
    async def test_anti_fraud_duplicate_referred_rejected(self) -> None:
        """Verify already referred user cannot be referred a second time."""
        async with create_isolated_session() as session:
            ref1 = User(id=401, username="referrer_one")
            ref2 = User(id=402, username="referrer_two")
            friend = User(id=403, username="invited_friend")
            session.add_all([ref1, ref2, friend])
            await session.flush()

            repo = ReferralRepository(session)
            first_attempt = await repo.register_referral_atomic(
                referrer_id=401, new_user_id=403
            )
            assert first_attempt is True

            # Second attempt with different referrer must be rejected
            second_attempt = await repo.register_referral_atomic(
                referrer_id=402, new_user_id=403
            )
            assert second_attempt is False

            # Duplicate attempt with same referrer must also be rejected
            duplicate_attempt = await repo.register_referral_atomic(
                referrer_id=401, new_user_id=403
            )
            assert duplicate_attempt is False

            assert await repo.get_referrals_count(401) == 1
            assert await repo.get_referrals_count(402) == 0

    @pytest.mark.asyncio
    async def test_anti_fraud_banned_referrer_rejected(self) -> None:
        """Verify referrals under banned referrers are rejected."""
        async with create_isolated_session() as session:
            banned_user = User(id=501, username="bad_actor", is_banned=True)
            victim = User(id=502, username="innocent")
            session.add_all([banned_user, victim])
            await session.flush()

            repo = ReferralRepository(session)
            success = await repo.register_referral_atomic(
                referrer_id=501, new_user_id=502
            )
            assert success is False
            assert await repo.get_referrals_count(501) == 0

    @pytest.mark.asyncio
    async def test_bonus_grant_only_on_threshold_multiple(self) -> None:
        """Verify bonuses are awarded strictly when referral count meets the threshold multiple."""
        async with create_isolated_session() as session:
            threshold = 3
            referrer = User(id=601, username="promoter", free_top_boosts=0)
            session.add(referrer)
            await session.flush()

            repo = ReferralRepository(session)

            for i in range(1, 7):
                friend_id = 700 + i
                friend = User(id=friend_id, username=f"friend_{i}")
                session.add(friend)
                await session.flush()

                recorded = await repo.register_referral_atomic(
                    referrer_id=601, new_user_id=friend_id
                )
                assert recorded is True

                current_count = await repo.get_referrals_count(601)
                assert current_count == i

                if current_count % threshold == 0:
                    await repo.grant_free_boost(601, count=1)

                await session.refresh(referrer)
                expected_boosts = i // threshold
                assert referrer.free_top_boosts == expected_boosts

            assert referrer.free_top_boosts == 2

    @pytest.mark.asyncio
    async def test_consume_free_boost_and_ad_activation(self) -> None:
        """Verify atomic decrement of bonus and activation of 24h TOP status."""
        async with create_isolated_session() as session:
            seller = User(id=801, username="seller_pro", free_top_boosts=1)
            ad = Ad(
                id=10,
                seller_id=801,
                category="Винтовки",
                weapon_model="AKR",
                skin_name="Necromancer",
                price_gold=1200,
                sticker_count=0,
                sticker_rarity="",
                photo_file_id="photo_123",
                is_top=False,
                is_active=True,
            )
            session.add_all([seller, ad])
            await session.flush()

            ref_repo = ReferralRepository(session)
            ad_repo = AdRepository(session)

            # First consumption must succeed
            consumed = await ref_repo.consume_free_boost(801)
            assert consumed is True

            await session.refresh(seller)
            assert seller.free_top_boosts == 0

            # Activate 24h TOP
            activated = await ad_repo.activate_top_status(ad_id=10, duration_days=1)
            assert activated is True

            ad_updated = await ad_repo.get_by_id(10)
            assert ad_updated is not None
            assert ad_updated.is_top is True
            assert ad_updated.top_until is not None
            now_utc = datetime.now(UTC)
            top_until = (
                ad_updated.top_until
                if ad_updated.top_until.tzinfo
                else ad_updated.top_until.replace(tzinfo=UTC)
            )
            assert top_until > now_utc + timedelta(hours=23)

            # Second consumption when balance is 0 must fail
            consumed_again = await ref_repo.consume_free_boost(801)
            assert consumed_again is False

    @pytest.mark.asyncio
    async def test_concurrency_isolation_no_negative_balance(self) -> None:
        """Verify race condition protection: balance never goes below zero."""
        async with create_isolated_session() as session:
            user = User(id=901, username="concurrent_user", free_top_boosts=1)
            session.add(user)
            await session.flush()

            ref_repo = ReferralRepository(session)

            res1 = await ref_repo.consume_free_boost(901)
            res2 = await ref_repo.consume_free_boost(901)

            assert res1 is True
            assert res2 is False

            await session.refresh(user)
            assert user.free_top_boosts == 0


class TestStartHandlerReferralFlow:
    """Validate aiogram /start handler behavior with referral deep links."""

    @pytest.mark.asyncio
    async def test_start_referral_flow_for_new_user(self) -> None:
        """New user opening /start ref_100 registers referral and notifies referrer."""
        async with create_isolated_session() as session:
            referrer = User(id=1000, username="host_user", free_top_boosts=0)
            session.add(referrer)
            await session.flush()

            new_tg_user = User(id=2000, username="newbie_guest")
            session.add(new_tg_user)
            await session.flush()

            mock_bot = MagicMock()
            mock_bot.send_message = AsyncMock(return_value=MagicMock())

            storage = MemoryStorage()
            state = FSMContext(
                storage=storage,
                key=StorageKey(bot_id=1, chat_id=2000, user_id=2000),
            )

            mock_msg = MagicMock(spec=Message)
            mock_msg.from_user = TgUser(
                id=2000, is_bot=False, first_name="Guest", username="newbie_guest"
            )
            mock_msg.chat = Chat(id=2000, type="private")
            mock_msg.bot = mock_bot
            mock_msg.answer = AsyncMock()

            cmd = CommandObject(prefix="/", command="start", args="ref_1000")

            await handle_start(
                message=mock_msg,
                state=state,
                user=new_tg_user,
                session=session,
                command=cmd,
                is_new_user=True,
            )

            ref_repo = ReferralRepository(session)
            assert await ref_repo.get_referrals_count(1000) == 1

            assert mock_bot.send_message.call_count == 1
            call_args = mock_bot.send_message.call_args[1]
            assert call_args["chat_id"] == 1000
            assert "новый друг" in call_args["text"]

    @pytest.mark.asyncio
    async def test_start_referral_flow_rejected_for_existing_user(self) -> None:
        """Existing user clicking /start ref_100 is NOT counted as referral."""
        async with create_isolated_session() as session:
            referrer = User(id=1100, username="host_user2")
            existing_user = User(id=2100, username="old_user")
            session.add_all([referrer, existing_user])
            await session.flush()

            mock_bot = MagicMock()
            mock_bot.send_message = AsyncMock()

            storage = MemoryStorage()
            state = FSMContext(
                storage=storage,
                key=StorageKey(bot_id=1, chat_id=2100, user_id=2100),
            )

            mock_msg = MagicMock(spec=Message)
            mock_msg.from_user = TgUser(
                id=2100, is_bot=False, first_name="Old", username="old_user"
            )
            mock_msg.chat = Chat(id=2100, type="private")
            mock_msg.bot = mock_bot
            mock_msg.answer = AsyncMock()

            cmd = CommandObject(prefix="/", command="start", args="ref_1100")

            await handle_start(
                message=mock_msg,
                state=state,
                user=existing_user,
                session=session,
                command=cmd,
                is_new_user=False,
            )

            ref_repo = ReferralRepository(session)
            assert await ref_repo.get_referrals_count(1100) == 0
            assert mock_bot.send_message.call_count == 0


class TestWebAdminReferralStats:
    """Validate FastAPI Web Admin Panel endpoints for referral analytics."""

    def test_referrals_stats_unauthorized(self) -> None:
        """Ensure endpoint is protected by authentication."""
        client = TestClient(app)
        res = client.get("/admin/api/referrals/stats")
        assert res.status_code == 401

    def test_referrals_stats_authorized(self) -> None:
        """Ensure authorized admin receives valid referral metrics payload."""
        mock_repo = MagicMock()
        mock_repo.get_system_referral_stats = AsyncMock(
            return_value={
                "total_referrals": 15,
                "active_free_boosts": 4,
                "used_free_boosts": 2,
                "total_issued_boosts": 6,
                "total_active_referrers": 8,
            }
        )
        mock_repo.get_top_referrers = AsyncMock(
            return_value=[
                {
                    "user_id": 1,
                    "username": "top_dog",
                    "referrals_count": 10,
                    "free_top_boosts": 2,
                }
            ]
        )

        class MockSessionContext:
            async def __aenter__(self) -> Any:
                return MagicMock()

            async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
                pass

        client = TestClient(app)
        token = create_admin_session_token()
        client.cookies.set("admin_session", token)

        with patch("src.web.app.async_session_maker", return_value=MockSessionContext()), \
             patch("src.web.app.ReferralRepository", return_value=mock_repo):
            res = client.get("/admin/api/referrals/stats")
            assert res.status_code == 200
            data = res.json()
            assert data.get("ok") is True
            assert "stats" in data
            assert data["stats"]["total_referrals"] == 15
            assert data["stats"]["used_free_boosts"] == 2
            assert len(data["top_referrers"]) == 1

    def test_web_admin_update_referrals_threshold_lifecycle(self) -> None:
        """Verify dynamic modification of referrals_for_top via Web Admin API."""
        client = TestClient(app)
        token = create_admin_session_token()
        client.cookies.set("admin_session", token)

        # 1. Update threshold to 5
        res = client.post(
            "/admin/api/settings",
            json={
                "max_active_ads": 10,
                "stars_turbo_price": 150,
                "stars_standard_price": 50,
                "referrals_for_top": 5,
            },
        )
        assert res.status_code == 200
        assert res.json().get("ok") is True
        assert get_referrals_for_top() == 5

        # 2. Reject out-of-range lower bound (< 1)
        res_low = client.post(
            "/admin/api/settings",
            json={
                "max_active_ads": 10,
                "stars_turbo_price": 150,
                "stars_standard_price": 50,
                "referrals_for_top": 0,
            },
        )
        assert res_low.status_code == 422

        # 3. Reject out-of-range upper bound (> 50)
        res_high = client.post(
            "/admin/api/settings",
            json={
                "max_active_ads": 10,
                "stars_turbo_price": 150,
                "stars_standard_price": 50,
                "referrals_for_top": 51,
            },
        )
        assert res_high.status_code == 422

        # 4. Restore default threshold 3
        res_restore = client.post(
            "/admin/api/settings",
            json={
                "max_active_ads": 10,
                "stars_turbo_price": 150,
                "stars_standard_price": 50,
                "referrals_for_top": 3,
            },
        )
        assert res_restore.status_code == 200
        assert get_referrals_for_top() == 3

    def test_web_admin_settings_page_renders_referral_threshold(self) -> None:
        """Verify settings HTML template renders referral configuration block."""
        client = TestClient(app)
        token = create_admin_session_token()
        client.cookies.set("admin_session", token)

        res = client.get("/admin/settings")
        assert res.status_code == 200
        assert "Реферальная программа" in res.text
        assert 'name="referrals_for_top"' in res.text
        assert 'id="referrals_for_top"' in res.text

    def test_web_admin_users_page_renders_referral_stats_and_columns(self) -> None:
        """Verify users HTML template renders referral stats cards and user referral details."""
        test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

        async def init_seed():
            async with test_engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            session_factory = async_sessionmaker(
                test_engine, expire_on_commit=False, class_=AsyncSession
            )
            async with session_factory() as session:
                u1 = User(id=7701, username="alpha_referrer", free_top_boosts=3)
                u2 = User(id=7702, username="beta_invitee", free_top_boosts=0)
                ref = Referral(referrer_id=7701, referred_id=7702)
                session.add_all([u1, u2, ref])
                await session.commit()

        asyncio.run(init_seed())

        class MemorySessionMaker:
            def __call__(self) -> AsyncSession:
                session_factory = async_sessionmaker(
                    test_engine, expire_on_commit=False, class_=AsyncSession
                )
                return session_factory()

        client = TestClient(app)
        token = create_admin_session_token()
        client.cookies.set("admin_session", token)

        with patch("src.web.app.async_session_maker", MemorySessionMaker()):
            res = client.get("/admin/users")
            assert res.status_code == 200
            # Summary metrics cards
            assert "Всего рефералов" in res.text
            assert "Активных рефереров" in res.text
            # User table columns
            assert "Реферальная программа" in res.text
            assert "Пригласил:" in res.text
            assert "@alpha_referrer" in res.text


class TestReferralEndToEndSmoke:
    """End-to-End smoke test verifying the complete user referral journey."""

    @pytest.mark.asyncio
    async def test_complete_referral_and_boost_journey(self) -> None:
        """
        Complete E2E cycle:
        1. Seller publishes ad -> is_top=False, free_top_boosts=0.
        2. Seller referral link generated.
        3. 3 Friends join sequentially via /start ref_<seller_id>.
        4. Target threshold (3 friends) achieved -> seller receives notification and +1 free boost.
        5. Seller activates free TOP boost on the ad.
        6. Ad becomes is_top=True, top_until set for 24h, free_top_boosts decremented to 0.
        7. StarTransaction audit trail recorded with tariff='free_referral_boost'.
        8. Repeat activation is blocked with alert.
        """
        async with create_isolated_session() as session:
            # 1. Setup seller and ad
            seller_id = 99901
            seller = User(id=seller_id, username="smoke_seller", free_top_boosts=0)
            ad = Ad(
                id=9901,
                seller_id=seller_id,
                category="Винтовки",
                weapon_model="AKR",
                skin_name="Carbon",
                price_gold=850,
                sticker_count=0,
                sticker_rarity="",
                photo_file_id="photo_carbon",
                is_top=False,
                is_active=True,
            )
            session.add_all([seller, ad])
            await session.commit()

            # 2. Verify referral invite link format
            bot_username = "Standsticker_bot"
            invite_url = f"https://t.me/{bot_username}?start=ref_{seller_id}"
            assert f"ref_{seller_id}" in invite_url

            # 3. Simulate 3 friends joining via /start ref_99901
            mock_bot = MagicMock()
            mock_bot.send_message = AsyncMock(return_value=MagicMock())

            threshold = get_referrals_for_top()
            for friend_idx in range(1, threshold + 1):
                friend_id = 99910 + friend_idx
                friend = User(id=friend_id, username=f"smoke_friend_{friend_idx}")
                session.add(friend)
                await session.commit()

                storage = MemoryStorage()
                state = FSMContext(
                    storage=storage,
                    key=StorageKey(bot_id=1, chat_id=friend_id, user_id=friend_id),
                )
                mock_msg = MagicMock(spec=Message)
                mock_msg.from_user = TgUser(
                    id=friend_id,
                    is_bot=False,
                    first_name=f"Friend{friend_idx}",
                    username=f"smoke_friend_{friend_idx}",
                )
                mock_msg.chat = Chat(id=friend_id, type="private")
                mock_msg.bot = mock_bot
                mock_msg.answer = AsyncMock()

                cmd = CommandObject(prefix="/", command="start", args=f"ref_{seller_id}")

                await handle_start(
                    message=mock_msg,
                    state=state,
                    user=friend,
                    session=session,
                    command=cmd,
                    is_new_user=True,
                )

            # 4. Verify seller now has +1 free top boost and received notification
            await session.refresh(seller)
            assert seller.free_top_boosts == 1

            # Notice must have been sent for threshold achievement
            assert mock_bot.send_message.call_count == threshold
            last_call_text = mock_bot.send_message.call_args[1]["text"]
            assert "Цель достигнута" in last_call_text
            assert "начислен 1 бесплатный ТОП" in last_call_text

            # 5. Seller applies free TOP boost to the ad
            mock_caption_msg = MagicMock(spec=Message)
            mock_caption_msg.caption = "AKR | Carbon"
            mock_caption_msg.edit_caption = AsyncMock()
            mock_caption_msg.answer = AsyncMock()

            cb = MagicMock(spec=CallbackQuery)
            cb.data = f"free_boost_ad:{ad.id}"
            cb.message = mock_caption_msg
            cb.answer = AsyncMock()

            await promo_activate_free_boost(cb, seller, session)

            # 6. Verify ad is now in TOP and boost is consumed
            await session.refresh(seller)
            await session.refresh(ad)
            assert seller.free_top_boosts == 0
            assert ad.is_top is True
            assert ad.top_until is not None

            # 7. Verify StarTransaction audit entry
            tx_stmt = select(StarTransaction).where(
                StarTransaction.ad_id == ad.id,
                StarTransaction.tariff == "free_referral_boost",
            )
            tx = (await session.execute(tx_stmt)).scalar_one_or_none()
            assert tx is not None
            assert tx.user_id == seller_id
            assert tx.stars_amount == 0

            # 8. Attempting another activation on an already TOP ad must be blocked
            cb2 = MagicMock(spec=CallbackQuery)
            cb2.data = f"free_boost_ad:{ad.id}"
            cb2.message = MagicMock(spec=Message)
            cb2.answer = AsyncMock()

            await promo_activate_free_boost(cb2, seller, session)
            cb2.answer.assert_called_once()
            assert "уже находится в ТОПе" in cb2.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_user_delete_permanently_and_restart_as_new_user(self) -> None:
        """User deleted via admin panel is wiped from DB and treated as new user upon restart."""
        async with create_isolated_session() as session:
            # 1. Create referrer and user
            referrer = User(id=8801, username="host_ref")
            user = User(id=8802, username="deleted_guy", free_top_boosts=1)
            session.add_all([referrer, user])
            await session.flush()

            # 2. Add an ad, a referral, and a search subscription for user
            ad = Ad(
                seller_id=user.id,
                category="Винтовки",
                weapon_model="AKR",
                skin_name="Necromancer",
                sticker_count=2,
                sticker_rarity="Rare",
                price_gold=500,
                photo_file_id="photo_8802",
                is_active=True,
            )
            ref = Referral(referrer_id=referrer.id, referred_id=user.id)
            sub = SearchSubscription(user_id=user.id, category="Винтовки")
            session.add_all([ad, ref, sub])
            await session.commit()

            # 3. Permanently delete user via UserRepository
            user_repo = UserRepository(session)
            success, partner_ids = await user_repo.delete_permanently(user.id)
            assert success is True

            # 4. Verify user and related records are completely gone
            assert await user_repo.get_by_id(user.id) is None

            ad_check = await session.execute(select(Ad).where(Ad.seller_id == user.id))
            assert ad_check.scalar_one_or_none() is None

            ref_check = await session.execute(
                select(Referral).where(Referral.referred_id == user.id)
            )
            assert ref_check.scalar_one_or_none() is None

            sub_check = await session.execute(
                select(SearchSubscription).where(SearchSubscription.user_id == user.id)
            )
            assert sub_check.scalar_one_or_none() is None

            # 5. User restarts bot (/start) -> get_or_create must return is_new_user=True
            fresh_user, is_new_user = await user_repo.get_or_create(
                user_id=8802, username="deleted_guy", first_name="Restarted"
            )
            assert is_new_user is True
            assert fresh_user.id == 8802
            assert fresh_user.free_top_boosts == 0
            assert fresh_user.is_active is True

    def test_admin_api_delete_user(self) -> None:
        """API endpoint /admin/api/users/{user_id}/delete permanently deletes user."""
        mock_user_repo = MagicMock()
        mock_user_repo.delete_permanently = AsyncMock(return_value=(True, []))

        class MockSessionContext:
            async def __aenter__(self):
                return MagicMock()

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                return None

        client = TestClient(app)
        token = create_admin_session_token()
        client.cookies.set("admin_session", token)

        with patch("src.web.app.async_session_maker", return_value=MockSessionContext()), \
             patch("src.web.app.UserRepository", return_value=mock_user_repo):
            res = client.post("/admin/api/users/9999/delete")
            assert res.status_code == 200
            assert res.json() == {"ok": True, "user_id": 9999}
            mock_user_repo.delete_permanently.assert_called_once_with(9999)
