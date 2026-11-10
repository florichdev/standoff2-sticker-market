from datetime import UTC, datetime

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.database.models import ChatMessage, ChatSession, User


class ChatRepository:
    """Data access repository for anonymous proxy chats and seller reviews."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_active_session_for_user(self, user_id: int) -> ChatSession | None:
        """Find any currently active chat session where user is either buyer or seller."""
        statement = (
            select(ChatSession)
            .options(
                joinedload(ChatSession.ad),
                joinedload(ChatSession.buyer),
                joinedload(ChatSession.seller),
            )
            .where(
                ChatSession.is_active.is_(True),
                or_(
                    ChatSession.buyer_id == user_id,
                    ChatSession.seller_id == user_id,
                ),
            )
        )
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def get_active_session_by_id(self, session_id: int) -> ChatSession | None:
        """Fetch an active chat session by ID with eagerly loaded relations."""
        statement = (
            select(ChatSession)
            .options(
                joinedload(ChatSession.ad),
                joinedload(ChatSession.buyer),
                joinedload(ChatSession.seller),
            )
            .where(
                ChatSession.id == session_id,
                ChatSession.is_active.is_(True),
            )
        )
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def get_session_by_id(self, session_id: int) -> ChatSession | None:
        """Fetch any chat session by ID regardless of active status."""
        statement = (
            select(ChatSession)
            .options(
                joinedload(ChatSession.ad),
                joinedload(ChatSession.buyer),
                joinedload(ChatSession.seller),
            )
            .where(ChatSession.id == session_id)
        )
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def create_chat_session(
        self, ad_id: int, buyer_id: int, seller_id: int
    ) -> ChatSession:
        """Create and persist a new anonymous chat session."""
        chat_session = ChatSession(
            ad_id=ad_id,
            buyer_id=buyer_id,
            seller_id=seller_id,
            is_active=True,
        )
        self.session.add(chat_session)
        await self.session.flush()
        return chat_session

    async def get_user_active_sessions(self, user_id: int) -> list[ChatSession]:
        """Fetch all currently active chat sessions where user is either buyer or seller."""
        statement = (
            select(ChatSession)
            .options(
                joinedload(ChatSession.ad),
                joinedload(ChatSession.buyer),
                joinedload(ChatSession.seller),
            )
            .where(
                ChatSession.is_active.is_(True),
                or_(
                    ChatSession.buyer_id == user_id,
                    ChatSession.seller_id == user_id,
                ),
            )
            .order_by(ChatSession.created_at.desc())
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def get_chat_last_messages(
        self, session_id: int, limit: int = 5
    ) -> list[ChatMessage]:
        """Fetch the most recent messages for a chat session in chronological order."""
        statement = (
            select(ChatMessage)
            .where(ChatMessage.chat_id == session_id)
            .order_by(ChatMessage.sent_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(statement)
        messages = list(result.scalars().all())
        messages.reverse()
        return messages

    async def create_chat_session_guarded(
        self, ad_id: int, buyer_id: int, seller_id: int
    ) -> tuple[ChatSession | None, str | None]:
        """Atomically find existing active chat or create a new session under row locks."""
        user_ids = sorted([buyer_id, seller_id])
        lock_stmt = (
            select(User.id)
            .where(User.id.in_(user_ids))
            .order_by(User.id)
            .with_for_update()
        )
        await self.session.execute(lock_stmt)

        existing_stmt = (
            select(ChatSession)
            .options(
                joinedload(ChatSession.ad),
                joinedload(ChatSession.buyer),
                joinedload(ChatSession.seller),
            )
            .where(
                ChatSession.is_active.is_(True),
                ChatSession.ad_id == ad_id,
                ChatSession.buyer_id == buyer_id,
                ChatSession.seller_id == seller_id,
            )
        )
        existing = (await self.session.execute(existing_stmt)).scalars().first()
        if existing is not None:
            return existing, None

        chat_session = ChatSession(
            ad_id=ad_id,
            buyer_id=buyer_id,
            seller_id=seller_id,
            is_active=True,
        )
        self.session.add(chat_session)
        await self.session.flush()
        return await self.get_active_session_by_id(chat_session.id), None

    async def close_chat_session(self, session_id: int) -> ChatSession | None:
        """Atomically close an active chat session with closed_at timestamp."""
        now = datetime.now(UTC)
        statement = (
            update(ChatSession)
            .where(ChatSession.id == session_id, ChatSession.is_active.is_(True))
            .values(is_active=False, closed_at=now)
            .returning(ChatSession.id)
        )
        result = await self.session.execute(statement)
        closed_id = result.scalar_one_or_none()
        if closed_id is None:
            return None
        return await self.get_session_by_id(session_id)

    async def save_chat_message(
        self,
        session_id: int,
        sender_id: int,
        content_type: str,
        text_content: str | None = None,
        file_id: str | None = None,
    ) -> ChatMessage:
        """Persist a message into the audit log for dispute resolution."""
        message = ChatMessage(
            chat_id=session_id,
            sender_id=sender_id,
            content_type=content_type,
            text_content=text_content,
            file_id=file_id,
        )
        self.session.add(message)
        await self.session.flush()
        return message

    async def has_buyer_reviewed_seller(self, buyer_id: int, seller_id: int) -> bool:
        """Check if buyer has ever reviewed this seller across any chat session."""
        statement = (
            select(ChatMessage.id)
            .join(ChatSession, ChatMessage.chat_id == ChatSession.id)
            .where(
                ChatSession.seller_id == seller_id,
                ChatMessage.sender_id == buyer_id,
                ChatMessage.content_type == "review",
            )
            .limit(1)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none() is not None

    async def record_seller_review(
        self, session_id: int, seller_id: int, buyer_id: int, score: int
    ) -> tuple[bool, float]:
        """Record seller rating (1-5 ⭐) and atomically recalculate seller reputation under row lock.

        Ensures a buyer can only review the same seller at most once across the bot.
        Returns (is_success, updated_rating).
        """
        if score < 1 or score > 5:
            return False, 5.0

        chat_session = await self.get_session_by_id(session_id)
        if (
            chat_session is None
            or chat_session.buyer_id != buyer_id
            or chat_session.seller_id != seller_id
        ):
            return False, 5.0

        # Lock seller row first to prevent concurrent review race conditions (TOCTOU)
        lock_stmt = select(User).where(User.id == seller_id).with_for_update()
        seller_res = await self.session.execute(lock_stmt)
        seller = seller_res.scalar_one_or_none()
        if seller is None:
            return False, 5.0

        already_reviewed = await self.has_buyer_reviewed_seller(
            buyer_id=buyer_id, seller_id=seller_id
        )
        if already_reviewed:
            return False, seller.rating

        await self.save_chat_message(
            session_id=session_id,
            sender_id=buyer_id,
            content_type="review",
            text_content=str(score),
        )

        old_rating = seller.rating
        count = seller.ratings_count
        new_rating = round((float(old_rating) * count + float(score)) / (count + 1), 2)

        seller.rating = new_rating
        seller.ratings_count = count + 1
        await self.session.flush()

        return True, new_rating
