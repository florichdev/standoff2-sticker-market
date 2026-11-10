from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import Ad, SearchSubscription


class SubscriptionRepository:
    """Data access repository for buyer search subscriptions and notification matching."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def save_subscription(
        self,
        user_id: int,
        category: str | None = None,
        weapon_model: str | None = None,
        skin_name: str | None = None,
        sticker_count: int | None = None,
        sticker_rarity: str | None = None,
    ) -> SearchSubscription:
        """Persist a search filter subscription for automatic push alerts."""
        subscription = SearchSubscription(
            user_id=user_id,
            category=category,
            weapon_model=weapon_model,
            skin_name=skin_name,
            sticker_count=sticker_count,
            sticker_rarity=sticker_rarity,
        )
        self.session.add(subscription)
        await self.session.flush()
        return subscription

    async def find_matching_subscribers(self, ad: Ad) -> list[int]:
        """Find user IDs of buyers whose saved filter matches a newly created ad.

        Excludes the seller themselves to avoid self-notification.
        """
        statement = (
            select(SearchSubscription.user_id)
            .distinct()
            .where(
                SearchSubscription.user_id != ad.seller_id,
                or_(
                    SearchSubscription.category.is_(None),
                    SearchSubscription.category == ad.category,
                ),
                or_(
                    SearchSubscription.weapon_model.is_(None),
                    SearchSubscription.weapon_model == ad.weapon_model,
                ),
                or_(
                    SearchSubscription.skin_name.is_(None),
                    SearchSubscription.skin_name == ad.skin_name,
                ),
                or_(
                    SearchSubscription.sticker_count.is_(None),
                    SearchSubscription.sticker_count == ad.sticker_count,
                ),
                or_(
                    SearchSubscription.sticker_rarity.is_(None),
                    SearchSubscription.sticker_rarity == ad.sticker_rarity,
                ),
            )
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def get_user_subscriptions(self, user_id: int) -> list[SearchSubscription]:
        """Fetch all saved filter subscriptions for a user."""
        statement = (
            select(SearchSubscription)
            .where(SearchSubscription.user_id == user_id)
            .order_by(SearchSubscription.created_at.desc())
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def delete_subscription(self, subscription_id: int, user_id: int) -> bool:
        """Delete a saved search subscription (IDOR-safe)."""
        statement = (
            delete(SearchSubscription)
            .where(
                SearchSubscription.id == subscription_id,
                SearchSubscription.user_id == user_id,
            )
            .returning(SearchSubscription.id)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none() is not None

    async def clear_user_subscriptions(self, user_id: int) -> int:
        """Delete all saved search subscriptions for a user."""
        statement = (
            delete(SearchSubscription)
            .where(SearchSubscription.user_id == user_id)
            .returning(SearchSubscription.id)
        )
        result = await self.session.execute(statement)
        return len(list(result.scalars().all()))
