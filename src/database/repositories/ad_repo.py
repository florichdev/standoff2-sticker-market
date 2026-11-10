from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.database.models import Ad, StarTransaction, User


class AdRepository:
    """Data access repository for skin ads and promotions."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def count_active_seller_ads(self, seller_id: int) -> int:
        """Count currently active ads for a seller."""
        statement = select(func.count(Ad.id)).where(
            Ad.seller_id == seller_id, Ad.is_active.is_(True)
        )
        result = await self.session.execute(statement)
        return int(result.scalar_one())

    async def get_seller_statistics(self, seller_id: int) -> dict[str, int]:
        """Aggregate stats for seller: active_ads, total_ads, total_views, total_clicks."""
        statement = select(
            func.count(Ad.id).label("total_ads"),
            func.count(Ad.id).filter(Ad.is_active.is_(True)).label("active_ads"),
            func.coalesce(func.sum(Ad.views_count), 0).label("total_views"),
            func.coalesce(func.sum(Ad.clicks_count), 0).label("total_clicks"),
        ).where(Ad.seller_id == seller_id)
        result = await self.session.execute(statement)
        row = result.one()
        return {
            "total_ads": int(row.total_ads),
            "active_ads": int(row.active_ads),
            "total_views": int(row.total_views),
            "total_clicks": int(row.total_clicks),
        }

    async def create_ad(
        self,
        seller_id: int,
        category: str,
        weapon_model: str,
        skin_name: str,
        sticker_count: int,
        sticker_rarity: str,
        price_gold: int,
        photo_file_id: str,
    ) -> Ad:
        """Create and persist a new skin listing."""
        ad = Ad(
            seller_id=seller_id,
            category=category,
            weapon_model=weapon_model,
            skin_name=skin_name,
            sticker_count=sticker_count,
            sticker_rarity=sticker_rarity,
            price_gold=price_gold,
            photo_file_id=photo_file_id,
            is_active=True,
            is_top=False,
        )
        self.session.add(ad)
        await self.session.flush()
        return ad

    async def create_ad_guarded(
        self,
        seller_id: int,
        category: str,
        weapon_model: str,
        skin_name: str,
        sticker_count: int,
        sticker_rarity: str,
        price_gold: int,
        photo_file_id: str,
        max_limit: int = 3,
    ) -> Ad | None:
        """Atomically lock user row, verify active ads count, and create ad if within limit."""
        user_lock_stmt = select(User.id).where(User.id == seller_id).with_for_update()
        await self.session.execute(user_lock_stmt)

        count_stmt = select(func.count(Ad.id)).where(
            Ad.seller_id == seller_id, Ad.is_active.is_(True)
        )
        active_count = int((await self.session.execute(count_stmt)).scalar_one())
        if active_count >= max_limit:
            return None

        ad = Ad(
            seller_id=seller_id,
            category=category,
            weapon_model=weapon_model,
            skin_name=skin_name,
            sticker_count=sticker_count,
            sticker_rarity=sticker_rarity,
            price_gold=price_gold,
            photo_file_id=photo_file_id,
            is_active=True,
            is_top=False,
        )
        self.session.add(ad)
        await self.session.flush()
        return ad

    async def get_seller_ads(self, seller_id: int) -> list[Ad]:
        """Fetch all active listings for a specific seller."""
        statement = (
            select(Ad)
            .where(Ad.seller_id == seller_id, Ad.is_active.is_(True))
            .order_by(Ad.is_top.desc(), Ad.created_at.desc())
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def get_by_id(self, ad_id: int) -> Ad | None:
        """Fetch an ad by ID."""
        statement = select(Ad).where(Ad.id == ad_id)
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def delete_ad(self, ad_id: int, seller_id: int) -> bool:
        """Soft-delete an ad owned by seller (IDOR-safe)."""
        statement = (
            update(Ad)
            .where(Ad.id == ad_id, Ad.seller_id == seller_id, Ad.is_active.is_(True))
            .values(is_active=False)
            .returning(Ad.id)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none() is not None

    async def update_ad_price(
        self, ad_id: int, seller_id: int, new_price: int
    ) -> Ad | None:
        """Update price for an ad owned by seller."""
        ad = await self.get_by_id(ad_id)
        if ad is None or ad.seller_id != seller_id or not ad.is_active:
            return None
        ad.price_gold = new_price
        await self.session.flush()
        return ad

    async def activate_top_status(self, ad_id: int, duration_days: int) -> bool:
        """Boost ad to TOP status with expiration date."""
        top_until = datetime.now(UTC) + timedelta(days=duration_days)
        statement = (
            update(Ad)
            .where(Ad.id == ad_id, Ad.is_active.is_(True))
            .values(is_top=True, top_until=top_until)
            .returning(Ad.id)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none() is not None

    async def record_star_transaction(
        self,
        user_id: int,
        ad_id: int,
        tariff: str,
        stars_amount: int,
        telegram_payment_charge_id: str,
    ) -> StarTransaction:
        """Log a Telegram Stars payment transaction."""
        transaction = StarTransaction(
            user_id=user_id,
            ad_id=ad_id,
            tariff=tariff,
            stars_amount=stars_amount,
            telegram_payment_charge_id=telegram_payment_charge_id,
        )
        self.session.add(transaction)
        await self.session.flush()
        return transaction

    async def get_feed_ads(
        self,
        offset: int = 0,
        limit: int = 1,
        category: str | None = None,
        weapon_model: str | None = None,
        skin_name: str | None = None,
        sticker_count: int | None = None,
        sticker_rarity: str | None = None,
        price_sort: str | None = None,
    ) -> tuple[list[Ad], int]:
        """Fetch a paginated slice of active ads matching filter criteria and total count."""
        conditions: list[Any] = [Ad.is_active.is_(True)]

        if category:
            conditions.append(Ad.category == category)
        if weapon_model:
            conditions.append(Ad.weapon_model == weapon_model)
        if skin_name:
            conditions.append(Ad.skin_name == skin_name)
        if sticker_count is not None:
            conditions.append(Ad.sticker_count == sticker_count)
        if sticker_rarity:
            conditions.append(Ad.sticker_rarity == sticker_rarity)

        count_stmt = select(func.count(Ad.id)).where(*conditions)
        total_count = int((await self.session.execute(count_stmt)).scalar_one())

        query = select(Ad).options(joinedload(Ad.seller)).where(*conditions)

        order_clauses: list[Any] = [Ad.is_top.desc()]
        if price_sort == "asc":
            order_clauses.append(Ad.price_gold.asc())
        elif price_sort == "desc":
            order_clauses.append(Ad.price_gold.desc())
        order_clauses.append(Ad.created_at.desc())

        query = query.order_by(*order_clauses).offset(offset).limit(limit)
        result = await self.session.execute(query)
        ads = list(result.scalars().all())

        return ads, total_count

    async def increment_ad_views(self, ad_id: int) -> None:
        """Atomically increment views counter for an ad."""
        statement = (
            update(Ad).where(Ad.id == ad_id).values(views_count=Ad.views_count + 1)
        )
        await self.session.execute(statement)

    async def increment_multiple_ad_views(self, ad_ids: list[int]) -> None:
        """Atomically increment views counter in a single query for multiple ads."""
        if not ad_ids:
            return
        statement = (
            update(Ad)
            .where(Ad.id.in_(ad_ids))
            .values(views_count=Ad.views_count + 1)
        )
        await self.session.execute(statement)

    async def increment_ad_clicks(self, ad_id: int) -> None:
        """Atomically increment clicks counter for an ad when buyer contacts seller."""
        statement = (
            update(Ad).where(Ad.id == ad_id).values(clicks_count=Ad.clicks_count + 1)
        )
        await self.session.execute(statement)
