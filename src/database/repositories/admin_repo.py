from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.config import settings
from src.database.models import Ad, ChatMessage, ChatSession, Report, User


class AdminRepository:
    """Data access repository for moderation, user banning, reports, and broadcast."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_report(
        self,
        reporter_id: int,
        violator_id: int,
        ad_id: int | None = None,
        chat_id: int | None = None,
        reason: str = "",
    ) -> Report:
        """Create and persist a user report."""
        report = Report(
            reporter_id=reporter_id,
            violator_id=violator_id,
            ad_id=ad_id,
            chat_id=chat_id,
            reason=reason,
            status="pending",
        )
        self.session.add(report)
        await self.session.flush()
        return report

    async def get_pending_reports(
        self, limit: int = 10, offset: int = 0
    ) -> tuple[list[Report], int]:
        """Fetch pending reports with total count and eagerly loaded relations."""
        count_stmt = select(func.count(Report.id)).where(Report.status == "pending")
        total = int((await self.session.execute(count_stmt)).scalar_one())

        query = (
            select(Report)
            .options(
                joinedload(Report.reporter),
                joinedload(Report.violator),
                joinedload(Report.ad),
            )
            .where(Report.status == "pending")
            .order_by(Report.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.execute(query)
        reports = list(result.scalars().all())
        return reports, total

    async def get_reports_history(
        self, offset: int = 0, limit: int = 5
    ) -> tuple[list[Report], int]:
        """Fetch resolved and dismissed reports with total count and eagerly loaded relations."""
        count_stmt = select(func.count(Report.id)).where(
            Report.status.in_(["resolved", "dismissed"])
        )
        total = int((await self.session.execute(count_stmt)).scalar_one())

        query = (
            select(Report)
            .options(
                joinedload(Report.reporter),
                joinedload(Report.violator),
                joinedload(Report.ad),
            )
            .where(Report.status.in_(["resolved", "dismissed"]))
            .order_by(Report.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.execute(query)
        reports = list(result.scalars().all())
        return reports, total

    async def count_reports_by_status(self) -> tuple[int, int]:
        """Return (pending_count, history_count)."""
        pending_stmt = select(func.count(Report.id)).where(Report.status == "pending")
        history_stmt = select(func.count(Report.id)).where(
            Report.status.in_(["resolved", "dismissed"])
        )
        pending = int((await self.session.execute(pending_stmt)).scalar_one())
        history = int((await self.session.execute(history_stmt)).scalar_one())
        return pending, history

    async def get_report_by_id(self, report_id: int) -> Report | None:
        """Fetch report with loaded relations by ID."""
        statement = (
            select(Report)
            .options(
                joinedload(Report.reporter),
                joinedload(Report.violator),
                joinedload(Report.ad),
            )
            .where(Report.id == report_id)
        )
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def resolve_report(self, report_id: int, status: str = "resolved") -> bool:
        """Mark report status ('resolved' or 'dismissed')."""
        statement = (
            update(Report)
            .where(Report.id == report_id)
            .values(status=status)
            .returning(Report.id)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none() is not None

    async def ban_user_cascade(self, user_id: int) -> tuple[bool, int, list[int]]:
        """Atomically ban a user, deactivate their active listings, and close active chats.

        Returns (is_banned, ads_deactivated_count, list_of_affected_chat_partner_ids).
        """
        if user_id in settings.ADMIN_IDS:
            return False, 0, []

        now = datetime.now(UTC)

        user_stmt = (
            update(User)
            .where(User.id == user_id)
            .values(is_banned=True)
            .returning(User.id)
        )
        user_res = await self.session.execute(user_stmt)
        if user_res.scalar_one_or_none() is None:
            return False, 0, []

        ad_stmt = (
            update(Ad)
            .where(Ad.seller_id == user_id, Ad.is_active.is_(True))
            .values(is_active=False)
            .returning(Ad.id)
        )
        ad_res = await self.session.execute(ad_stmt)
        ads_count = len(list(ad_res.scalars().all()))

        active_chats_stmt = select(ChatSession).where(
            or_(
                ChatSession.buyer_id == user_id,
                ChatSession.seller_id == user_id,
            ),
            ChatSession.is_active.is_(True),
        )
        chats_res = await self.session.execute(active_chats_stmt)
        open_chats = list(chats_res.scalars().all())

        partner_ids: list[int] = []
        if open_chats:
            chat_ids = [c.id for c in open_chats]
            for c in open_chats:
                partner_id = c.seller_id if c.buyer_id == user_id else c.buyer_id
                partner_ids.append(partner_id)

            close_chats_stmt = (
                update(ChatSession)
                .where(ChatSession.id.in_(chat_ids))
                .values(is_active=False, closed_at=now)
            )
            await self.session.execute(close_chats_stmt)

        return True, ads_count, partner_ids

    async def unban_user(self, user_id: int) -> bool:
        """Unban a previously banned user."""
        statement = (
            update(User)
            .where(User.id == user_id)
            .values(is_banned=False)
            .returning(User.id)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none() is not None

    async def get_user_by_id(self, user_id: int) -> User | None:
        """Fetch user by Telegram ID."""
        statement = select(User).where(User.id == user_id)
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def find_user_by_id_or_username(self, query: str) -> User | None:
        """Find a single user by numeric Telegram ID or @username with LIKE escaping."""
        clean = query.strip().lstrip("@")
        if not clean:
            return None

        if clean.isdigit():
            user = await self.get_user_by_id(int(clean))
            if user:
                return user

        escaped = clean.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

        stmt = select(User).where(User.username.ilike(escaped, escape="\\")).limit(1)
        res = await self.session.execute(stmt)
        user = res.scalar_one_or_none()
        if user:
            return user

        stmt = select(User).where(User.username.ilike(f"%{escaped}%", escape="\\")).limit(1)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_chat_logs(self, chat_id: int) -> list[ChatMessage]:
        """Fetch message audit history for a chat session."""
        statement = (
            select(ChatMessage)
            .where(ChatMessage.chat_id == chat_id)
            .order_by(ChatMessage.sent_at.asc())
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def get_all_active_user_ids(self) -> list[int]:
        """Fetch list of all non-banned and active user IDs for mass broadcasts."""
        statement = select(User.id).where(
            User.is_banned.is_(False),
            or_(User.is_active.is_(True), User.is_active.is_(None)),
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def get_system_stats(self) -> dict[str, int]:
        """Fetch aggregate marketplace metrics for admin dashboard."""
        users_count = int(
            (await self.session.execute(select(func.count(User.id)))).scalar_one()
        )
        banned_count = int(
            (
                await self.session.execute(
                    select(func.count(User.id)).where(User.is_banned.is_(True))
                )
            ).scalar_one()
        )
        ads_count = int(
            (
                await self.session.execute(
                    select(func.count(Ad.id)).where(Ad.is_active.is_(True))
                )
            ).scalar_one()
        )
        top_ads_count = int(
            (
                await self.session.execute(
                    select(func.count(Ad.id)).where(
                        Ad.is_active.is_(True), Ad.is_top.is_(True)
                    )
                )
            ).scalar_one()
        )
        pending_reports = int(
            (
                await self.session.execute(
                    select(func.count(Report.id)).where(Report.status == "pending")
                )
            ).scalar_one()
        )
        active_chats = int(
            (
                await self.session.execute(
                    select(func.count(ChatSession.id)).where(
                        ChatSession.is_active.is_(True)
                    )
                )
            ).scalar_one()
        )

        return {
            "users_total": users_count,
            "users_banned": banned_count,
            "ads_active": ads_count,
            "ads_top": top_ads_count,
            "reports_pending": pending_reports,
            "chats_active": active_chats,
        }

    async def get_all_reports_filtered(
        self, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> tuple[list[Report], int]:
        """Fetch reports filtered by status with total count."""
        conditions = []
        if status and status != "all":
            conditions.append(Report.status == status)

        count_stmt = select(func.count(Report.id))
        if conditions:
            count_stmt = count_stmt.where(*conditions)
        total = int((await self.session.execute(count_stmt)).scalar_one())

        query = (
            select(Report)
            .options(
                joinedload(Report.reporter),
                joinedload(Report.violator),
                joinedload(Report.ad),
            )
            .order_by(Report.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        if conditions:
            query = query.where(*conditions)

        result = await self.session.execute(query)
        return list(result.scalars().all()), total

    async def get_users_list(
        self, search: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[User]:
        """Fetch users with optional search by ID or username."""
        query = (
            select(User)
            .order_by(User.registration_date.desc())
            .offset(offset)
            .limit(limit)
        )
        if search:
            search_str = search.strip().lstrip("@")
            escaped_str = search_str.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            if search_str.isdigit():
                query = query.where(
                    or_(
                        User.id == int(search_str),
                        User.username.ilike(f"%{escaped_str}%", escape="\\"),
                    )
                )
            else:
                query = query.where(User.username.ilike(f"%{escaped_str}%", escape="\\"))
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_all_ads_filtered(
        self,
        filter_type: str = "active",
        seller_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Ad]:
        """Fetch ads with filter (active, top, all, seller_id)."""
        query = (
            select(Ad)
            .order_by(Ad.is_top.desc(), Ad.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        if seller_id is not None:
            query = query.where(Ad.seller_id == seller_id)
        if filter_type == "active":
            query = query.where(Ad.is_active.is_(True))
        elif filter_type == "top":
            query = query.where(Ad.is_active.is_(True), Ad.is_top.is_(True))
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_ads_realtime_status(
        self,
        filter_type: str = "active",
        seller_id: int | None = None,
        last_known_id: int = 0,
    ) -> dict[str, Any]:
        """Fetch latest active ad ID, total matching count, and any new ads since last_known_id."""
        conditions = []
        if seller_id is not None:
            conditions.append(Ad.seller_id == seller_id)
        if filter_type == "active":
            conditions.append(Ad.is_active.is_(True))
        elif filter_type == "top":
            conditions.append(Ad.is_active.is_(True), Ad.is_top.is_(True))

        summary_stmt = select(
            func.coalesce(func.max(Ad.id), 0).label("max_id"),
            func.count(Ad.id).label("total_count"),
        )
        if conditions:
            summary_stmt = summary_stmt.where(*conditions)

        res = (await self.session.execute(summary_stmt)).one()
        max_id = int(res.max_id)
        total_count = int(res.total_count)

        new_ads: list[Ad] = []
        if last_known_id > 0 and max_id > last_known_id:
            new_query = (
                select(Ad)
                .where(Ad.id > last_known_id, *conditions)
                .order_by(Ad.id.desc())
                .limit(20)
            )
            new_res = await self.session.execute(new_query)
            new_ads = list(new_res.scalars().all())

        return {
            "latest_ad_id": max_id,
            "total_count": total_count,
            "has_new": (max_id > last_known_id and last_known_id > 0),
            "new_ads": new_ads,
        }

    async def get_ad_with_seller(self, ad_id: int) -> Ad | None:
        """Fetch ad with eagerly loaded seller relation."""
        statement = select(Ad).options(joinedload(Ad.seller)).where(Ad.id == ad_id)
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def get_ad_reports(self, ad_id: int) -> list[Report]:
        """Fetch all reports filed against a specific ad."""
        statement = (
            select(Report)
            .options(joinedload(Report.reporter))
            .where(Report.ad_id == ad_id)
            .order_by(Report.created_at.desc())
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def deactivate_ad_as_admin(self, ad_id: int) -> bool:
        """Deactivate ad as an administrator without seller_id check."""
        stmt = update(Ad).where(Ad.id == ad_id).values(is_active=False).returning(Ad.id)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none() is not None

    async def count_seller_active_ads(self, seller_id: int) -> int:
        """Count currently active ads for a seller."""
        stmt = select(func.count(Ad.id)).where(
            Ad.seller_id == seller_id, Ad.is_active.is_(True)
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def activate_ad_as_admin(
        self, ad_id: int, max_limit: int = 3
    ) -> tuple[bool, str, int]:
        """Reactivate an ad as admin while strictly enforcing the seller active ads limit."""
        ad = await self.session.get(Ad, ad_id)
        if ad is None:
            return False, "Объявление не найдено.", 0

        current_active = await self.count_seller_active_ads(ad.seller_id)

        if ad.is_active:
            return True, "Объявление уже активно.", current_active

        if current_active >= max_limit:
            return (
                False,
                (
                    f"Невозможно активировать: у продавца ID {ad.seller_id} уже активно {current_active} из {max_limit} лотов. "
                    "Чтобы соблюсти лимит, сначала снимите с продажи другое объявление этого продавца."
                ),
                current_active,
            )

        ad.is_active = True
        await self.session.flush()
        return True, "Объявление успешно активировано!", current_active + 1

    async def toggle_ad_top_as_admin(
        self, ad_id: int, duration_days: int = 3
    ) -> tuple[bool, bool]:
        """Toggle TOP promotion status for an ad. Returns (success, new_is_top)."""
        ad = await self.session.get(Ad, ad_id)
        if not ad:
            return False, False
        if ad.is_top:
            ad.is_top = False
            ad.top_until = None
        else:
            ad.is_top = True
            ad.top_until = datetime.now(UTC) + timedelta(days=duration_days)
        await self.session.flush()
        return True, ad.is_top

    async def get_chat_session(self, chat_id: int) -> ChatSession | None:
        """Fetch chat session by ID."""
        stmt = select(ChatSession).where(ChatSession.id == chat_id)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()
