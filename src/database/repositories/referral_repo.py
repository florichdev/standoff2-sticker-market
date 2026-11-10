from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.database.models import Referral, StarTransaction, User


class ReferralRepository:
    """Data access repository for referral tracking and free TOP boosts."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def register_referral_atomic(
        self, referrer_id: int, new_user_id: int
    ) -> bool:
        """Atomically record a referral relationship with anti-fraud validations.

        Checks:
        1. User cannot invite themselves.
        2. Referrer must exist in database and not be banned.
        3. Referred user must not have been referred previously.
        """
        if referrer_id == new_user_id:
            return False

        referrer_stmt = select(User).where(User.id == referrer_id)
        referrer = (await self.session.execute(referrer_stmt)).scalar_one_or_none()
        if referrer is None or referrer.is_banned:
            return False

        existing_stmt = select(Referral.id).where(Referral.referred_id == new_user_id)
        existing = (await self.session.execute(existing_stmt)).scalar_one_or_none()
        if existing is not None:
            return False

        referral = Referral(
            referrer_id=referrer_id,
            referred_id=new_user_id,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(referral)
                await self.session.flush()
            return True
        except IntegrityError:
            return False

    async def get_referrals_count(self, user_id: int) -> int:
        """Count total confirmed unique friends invited by a specific user."""
        statement = select(func.count(Referral.id)).where(
            Referral.referrer_id == user_id
        )
        result = await self.session.execute(statement)
        return int(result.scalar_one() or 0)

    async def get_referral_source(self, user_id: int) -> Referral | None:
        """Find who referred this user, if anyone."""
        statement = (
            select(Referral)
            .options(joinedload(Referral.referrer))
            .where(Referral.referred_id == user_id)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def grant_free_boost(self, user_id: int, count: int = 1) -> int:
        """Atomically award free TOP boosts to a user and return the new balance."""
        lock_stmt = select(User).where(User.id == user_id).with_for_update()
        res = await self.session.execute(lock_stmt)
        user = res.scalar_one_or_none()
        if user is None:
            return 0

        user.free_top_boosts = (user.free_top_boosts or 0) + count
        await self.session.flush()
        return user.free_top_boosts

    async def consume_free_boost(self, user_id: int) -> bool:
        """Atomically decrement 1 free TOP boost if balance > 0 under row lock."""
        lock_stmt = select(User).where(User.id == user_id).with_for_update()
        res = await self.session.execute(lock_stmt)
        user = res.scalar_one_or_none()
        if user is None or user.free_top_boosts <= 0:
            return False

        user.free_top_boosts -= 1
        await self.session.flush()
        return True

    async def get_top_referrers(self, limit: int = 50) -> list[dict[str, Any]]:
        """Fetch top user referrers ordered by successful invitations."""
        statement = (
            select(
                User.id,
                User.username,
                User.first_name,
                User.free_top_boosts,
                func.count(Referral.id).label("referrals_count"),
            )
            .join(Referral, Referral.referrer_id == User.id)
            .group_by(User.id)
            .order_by(func.count(Referral.id).desc())
            .limit(limit)
        )
        result = await self.session.execute(statement)
        rows = result.all()
        return [
            {
                "user_id": r.id,
                "username": r.username,
                "first_name": r.first_name,
                "free_top_boosts": r.free_top_boosts,
                "referrals_count": int(r.referrals_count),
            }
            for r in rows
        ]

    async def get_system_referral_stats(self) -> dict[str, int]:
        """Aggregate platform-wide referral metrics."""
        total_stmt = select(func.count(Referral.id))
        total_referrals = int((await self.session.execute(total_stmt)).scalar_one() or 0)

        boosts_stmt = select(func.coalesce(func.sum(User.free_top_boosts), 0))
        active_boosts = int((await self.session.execute(boosts_stmt)).scalar_one() or 0)

        referrers_stmt = select(func.count(func.distinct(Referral.referrer_id)))
        total_referrers = int((await self.session.execute(referrers_stmt)).scalar_one() or 0)

        used_boosts_stmt = select(func.count(StarTransaction.id)).where(
            StarTransaction.tariff == "free_referral_boost"
        )
        used_boosts = int((await self.session.execute(used_boosts_stmt)).scalar_one() or 0)

        return {
            "total_referrals": total_referrals,
            "active_free_boosts": active_boosts,
            "used_free_boosts": used_boosts,
            "total_issued_boosts": active_boosts + used_boosts,
            "total_active_referrers": total_referrers,
        }

