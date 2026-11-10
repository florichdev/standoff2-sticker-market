from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import (
    Ad,
    AdBroadcastMessage,
    ChatMessage,
    ChatSession,
    Referral,
    Report,
    SearchSubscription,
    StarTransaction,
    User,
)


class UserRepository:
    """Data access repository for User entities."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, user_id: int) -> User | None:
        """Fetch a user by their Telegram ID."""
        statement = select(User).where(User.id == user_id)
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def get_or_create(
        self,
        user_id: int,
        username: str | None,
        first_name: str | None,
    ) -> tuple[User, bool]:
        """Get or create a user, updating profile info if changed.

        Returns a tuple of (user, created_flag).
        """
        user = await self.get_by_id(user_id)
        if user is not None:
            updated = False
            if user.username != username or user.first_name != first_name:
                user.username = username
                user.first_name = first_name
                updated = True
            if not user.is_active:
                user.is_active = True
                updated = True
            if updated:
                await self.session.flush()
            return user, False

        user = User(
            id=user_id,
            username=username,
            first_name=first_name,
            is_active=True,
        )
        self.session.add(user)
        await self.session.flush()
        return user, True

    async def update_profile(
        self,
        user_id: int,
        username: str | None,
        first_name: str | None,
    ) -> None:
        """Update username and first_name for an existing user."""
        statement = (
            update(User)
            .where(User.id == user_id)
            .values(username=username, first_name=first_name)
        )
        await self.session.execute(statement)

    async def check_is_banned(self, user_id: int) -> bool:
        """Check if a user is currently banned."""
        statement = select(User.is_banned).where(User.id == user_id)
        result = await self.session.execute(statement)
        is_banned = result.scalar_one_or_none()
        return bool(is_banned)

    async def set_ban_status(self, user_id: int, is_banned: bool) -> bool:
        """Update ban status for a user. Returns True if user was found."""
        statement = (
            update(User)
            .where(User.id == user_id)
            .values(is_banned=is_banned)
            .returning(User.id)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none() is not None

    async def set_active_status(self, user_id: int, is_active: bool) -> bool:
        """Update active status for a user (e.g. False if user blocked the bot)."""
        statement = (
            update(User)
            .where(User.id == user_id)
            .values(is_active=is_active)
            .returning(User.id)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none() is not None

    async def set_active_status_bulk(
        self, user_ids: list[int] | set[int], is_active: bool
    ) -> int:
        """Update active status for multiple users in a single atomic SQL statement."""
        unique_ids = list(user_ids)
        if not unique_ids:
            return 0
        statement = (
            update(User)
            .where(User.id.in_(unique_ids))
            .values(is_active=is_active)
            .returning(User.id)
        )
        result = await self.session.execute(statement)
        return len(result.scalars().all())

    async def delete_permanently(self, user_id: int) -> tuple[bool, list[int]]:
        """Completely delete user and all associated records from database.

        Returns a tuple of (success, list_of_affected_active_chat_partners).
        """
        # 0. Find active chat partners before deletion to notify them
        active_chats_stmt = select(ChatSession).where(
            or_(ChatSession.buyer_id == user_id, ChatSession.seller_id == user_id),
            ChatSession.is_active.is_(True),
        )
        active_chats_res = await self.session.execute(active_chats_stmt)
        active_chats = active_chats_res.scalars().all()
        partner_ids = [
            c.seller_id if c.buyer_id == user_id else c.buyer_id
            for c in active_chats
        ]

        # 1. Referrals (both as referrer and referred)
        await self.session.execute(
            delete(Referral).where(
                or_(Referral.referrer_id == user_id, Referral.referred_id == user_id)
            )
        )

        # 2. Search subscriptions
        await self.session.execute(
            delete(SearchSubscription).where(SearchSubscription.user_id == user_id)
        )

        # 3. Reports (both filed by user and filed against user)
        await self.session.execute(
            delete(Report).where(
                or_(Report.reporter_id == user_id, Report.violator_id == user_id)
            )
        )

        # 4. Star transactions
        await self.session.execute(
            delete(StarTransaction).where(StarTransaction.user_id == user_id)
        )

        # 5. Chats where user was buyer or seller (and their messages)
        chat_ids_stmt = select(ChatSession.id).where(
            or_(ChatSession.buyer_id == user_id, ChatSession.seller_id == user_id)
        )
        chat_ids_res = await self.session.execute(chat_ids_stmt)
        chat_ids = chat_ids_res.scalars().all()
        if chat_ids:
            await self.session.execute(
                delete(ChatMessage).where(ChatMessage.chat_id.in_(chat_ids))
            )
            await self.session.execute(
                delete(ChatSession).where(ChatSession.id.in_(chat_ids))
            )

        # 6. Any other messages sent by user
        await self.session.execute(
            delete(ChatMessage).where(ChatMessage.sender_id == user_id)
        )

        # 7. Ads created by user (and any chats/reports/transactions linked to these ads)
        ad_ids_stmt = select(Ad.id).where(Ad.seller_id == user_id)
        ad_ids_res = await self.session.execute(ad_ids_stmt)
        ad_ids = [int(i) for i in ad_ids_res.scalars().all()]
        if ad_ids:
            b_stmt = select(
                AdBroadcastMessage.chat_id, AdBroadcastMessage.message_id
            ).where(AdBroadcastMessage.ad_id.in_(ad_ids))
            b_res = await self.session.execute(b_stmt)
            broadcast_messages = [(int(r[0]), int(r[1])) for r in b_res.all()]
            if broadcast_messages:
                try:
                    from src.services.autopost import schedule_ad_broadcast_cleanup

                    schedule_ad_broadcast_cleanup(None, direct_messages=broadcast_messages)
                except Exception:
                    pass

            linked_chats_stmt = select(ChatSession.id).where(ChatSession.ad_id.in_(ad_ids))
            linked_chats_res = await self.session.execute(linked_chats_stmt)
            linked_chats = linked_chats_res.scalars().all()
            if linked_chats:
                await self.session.execute(
                    delete(ChatMessage).where(ChatMessage.chat_id.in_(linked_chats))
                )
                await self.session.execute(
                    delete(ChatSession).where(ChatSession.id.in_(linked_chats))
                )
            await self.session.execute(
                delete(Report).where(Report.ad_id.in_(ad_ids))
            )
            await self.session.execute(
                delete(StarTransaction).where(StarTransaction.ad_id.in_(ad_ids))
            )
            await self.session.execute(
                delete(AdBroadcastMessage).where(AdBroadcastMessage.ad_id.in_(ad_ids))
            )
            await self.session.execute(
                delete(Ad).where(Ad.id.in_(ad_ids))
            )

        # 8. Finally delete the user record
        res = await self.session.execute(
            delete(User).where(User.id == user_id).returning(User.id)
        )
        deleted_id = res.scalar_one_or_none()
        await self.session.commit()
        return (deleted_id is not None), partner_ids
