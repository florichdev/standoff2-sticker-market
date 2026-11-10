from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.base import Base


class User(Base):
    """User profile stored upon registration via /start."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    registration_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    is_banned: Mapped[bool] = mapped_column(
        Boolean, default=False, index=True, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, index=True, nullable=False
    )
    rating: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    ratings_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    free_top_boosts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    ads: Mapped[list["Ad"]] = relationship(
        back_populates="seller",
        cascade="all, delete-orphan",
    )
    subscriptions: Mapped[list["SearchSubscription"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    buyer_chats: Mapped[list["ChatSession"]] = relationship(
        foreign_keys="[ChatSession.buyer_id]",
        back_populates="buyer",
    )
    seller_chats: Mapped[list["ChatSession"]] = relationship(
        foreign_keys="[ChatSession.seller_id]",
        back_populates="seller",
    )
    reports_filed: Mapped[list["Report"]] = relationship(
        foreign_keys="[Report.reporter_id]",
        back_populates="reporter",
    )
    star_transactions: Mapped[list["StarTransaction"]] = relationship(
        back_populates="user",
    )
    referrals_made: Mapped[list["Referral"]] = relationship(
        foreign_keys="[Referral.referrer_id]",
        back_populates="referrer",
        cascade="all, delete-orphan",
    )
    referral_source: Mapped["Referral | None"] = relationship(
        foreign_keys="[Referral.referred_id]",
        back_populates="referred",
        uselist=False,
    )


class Ad(Base):
    """Skin listing created by sellers."""

    __tablename__ = "ads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    seller_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    category: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    weapon_model: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    skin_name: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    sticker_count: Mapped[int] = mapped_column(
        SmallInteger,
        CheckConstraint(
            "sticker_count >= 0 AND sticker_count <= 4",
            name="check_sticker_count_range",
        ),
        index=True,
        nullable=False,
    )
    sticker_rarity: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    price_gold: Mapped[int] = mapped_column(
        Integer,
        CheckConstraint("price_gold > 0", name="check_price_gold_positive"),
        index=True,
        nullable=False,
    )
    photo_file_id: Mapped[str] = mapped_column(String(255), nullable=False)
    views_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    clicks_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_top: Mapped[bool] = mapped_column(
        Boolean, default=False, index=True, nullable=False
    )
    top_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        index=True,
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, index=True, nullable=False
    )

    __table_args__ = (
        Index("ix_ads_active_top_created", "is_active", "is_top", "created_at"),
        Index(
            "ix_ads_filter_lookup", "category", "weapon_model", "skin_name", "is_active"
        ),
    )

    seller: Mapped["User"] = relationship(back_populates="ads")
    chats: Mapped[list["ChatSession"]] = relationship(back_populates="ad")
    reports: Mapped[list["Report"]] = relationship(back_populates="ad")
    broadcast_messages: Mapped[list["AdBroadcastMessage"]] = relationship(
        back_populates="ad",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class SearchSubscription(Base):
    """User search filters saved for instant notification matching."""

    __tablename__ = "search_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    weapon_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    skin_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sticker_count: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    sticker_rarity: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    user: Mapped["User"] = relationship(back_populates="subscriptions")


class ChatSession(Base):
    """Anonymous proxy chat session between buyer and seller."""

    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ad_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("ads.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    buyer_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    seller_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, index=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_chat_sessions_buyer_active", "buyer_id", "is_active"),
        Index("ix_chat_sessions_seller_active", "seller_id", "is_active"),
    )

    ad: Mapped["Ad | None"] = relationship(back_populates="chats")
    buyer: Mapped["User"] = relationship(
        foreign_keys=[buyer_id],
        back_populates="buyer_chats",
    )
    seller: Mapped["User"] = relationship(
        foreign_keys=[seller_id],
        back_populates="seller_chats",
    )
    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="chat",
        cascade="all, delete-orphan",
    )


class ChatMessage(Base):
    """Audit log of proxied messages for conflict resolution."""

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    sender_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    content_type: Mapped[str] = mapped_column(
        String(32), default="text", nullable=False
    )
    text_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        index=True,
        nullable=False,
    )

    chat: Mapped["ChatSession"] = relationship(back_populates="messages")


class Report(Base):
    """User complaints about scams or policy violations."""

    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reporter_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    violator_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    ad_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("ads.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    chat_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("chat_sessions.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default="pending", index=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    reporter: Mapped["User"] = relationship(
        foreign_keys=[reporter_id],
        back_populates="reports_filed",
    )
    violator: Mapped["User"] = relationship(foreign_keys=[violator_id])
    ad: Mapped["Ad | None"] = relationship(back_populates="reports")


class StarTransaction(Base):
    """Ledger of Telegram Stars transactions for ad promotions."""

    __tablename__ = "star_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    ad_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("ads.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    tariff: Mapped[str] = mapped_column(String(32), nullable=False)
    stars_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    telegram_payment_charge_id: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    user: Mapped["User"] = relationship(back_populates="star_transactions")
    ad: Mapped["Ad"] = relationship()


class Referral(Base):
    """User invitation record linking referrer and newly registered user."""

    __tablename__ = "referrals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    referrer_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    referred_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    referrer: Mapped["User"] = relationship(
        foreign_keys=[referrer_id],
        back_populates="referrals_made",
    )
    referred: Mapped["User"] = relationship(
        foreign_keys=[referred_id],
        back_populates="referral_source",
    )


class AdBroadcastMessage(Base):
    """Audit log of broadcast and push messages sent for an ad to allow automated cleanup upon ban or deactivation."""

    __tablename__ = "ad_broadcast_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ad_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("ads.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    ad: Mapped["Ad"] = relationship(back_populates="broadcast_messages")
