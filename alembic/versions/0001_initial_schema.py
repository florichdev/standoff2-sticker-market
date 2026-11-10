"""initial_schema

Revision ID: 0001
Revises:
Create Date: 2026-10-28 15:35:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. users
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("first_name", sa.String(length=128), nullable=True),
        sa.Column("registration_date", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("is_banned", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("rating", sa.Float(), server_default=sa.text("0.0"), nullable=False),
        sa.Column("ratings_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_is_banned"), "users", ["is_banned"], unique=False)

    # 2. ads
    op.create_table(
        "ads",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("seller_id", sa.BigInteger(), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("weapon_model", sa.String(length=64), nullable=False),
        sa.Column("skin_name", sa.String(length=64), nullable=False),
        sa.Column("sticker_count", sa.SmallInteger(), nullable=False),
        sa.Column("sticker_rarity", sa.String(length=32), nullable=False),
        sa.Column("price_gold", sa.Integer(), nullable=False),
        sa.Column("photo_file_id", sa.String(length=255), nullable=False),
        sa.Column("views_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("clicks_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("is_top", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("top_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.CheckConstraint("sticker_count >= 1 AND sticker_count <= 4", name="check_sticker_count_range"),
        sa.CheckConstraint("price_gold > 0", name="check_price_gold_positive"),
        sa.ForeignKeyConstraint(["seller_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ads_category"), "ads", ["category"], unique=False)
    op.create_index(op.f("ix_ads_created_at"), "ads", ["created_at"], unique=False)
    op.create_index(op.f("ix_ads_is_active"), "ads", ["is_active"], unique=False)
    op.create_index(op.f("ix_ads_is_top"), "ads", ["is_top"], unique=False)
    op.create_index(op.f("ix_ads_price_gold"), "ads", ["price_gold"], unique=False)
    op.create_index(op.f("ix_ads_seller_id"), "ads", ["seller_id"], unique=False)
    op.create_index(op.f("ix_ads_skin_name"), "ads", ["skin_name"], unique=False)
    op.create_index(op.f("ix_ads_sticker_count"), "ads", ["sticker_count"], unique=False)
    op.create_index(op.f("ix_ads_sticker_rarity"), "ads", ["sticker_rarity"], unique=False)
    op.create_index(op.f("ix_ads_top_until"), "ads", ["top_until"], unique=False)
    op.create_index(op.f("ix_ads_weapon_model"), "ads", ["weapon_model"], unique=False)
    op.create_index("ix_ads_active_top_created", "ads", ["is_active", "is_top", "created_at"], unique=False)
    op.create_index("ix_ads_filter_lookup", "ads", ["category", "weapon_model", "skin_name", "is_active"], unique=False)

    # 3. search_subscriptions
    op.create_table(
        "search_subscriptions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=True),
        sa.Column("weapon_model", sa.String(length=64), nullable=True),
        sa.Column("skin_name", sa.String(length=64), nullable=True),
        sa.Column("sticker_count", sa.SmallInteger(), nullable=True),
        sa.Column("sticker_rarity", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_search_subscriptions_user_id"), "search_subscriptions", ["user_id"], unique=False)

    # 4. chat_sessions
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ad_id", sa.Integer(), nullable=True),
        sa.Column("buyer_id", sa.BigInteger(), nullable=False),
        sa.Column("seller_id", sa.BigInteger(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["ad_id"], ["ads.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["buyer_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["seller_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_chat_sessions_ad_id"), "chat_sessions", ["ad_id"], unique=False)
    op.create_index(op.f("ix_chat_sessions_buyer_id"), "chat_sessions", ["buyer_id"], unique=False)
    op.create_index(op.f("ix_chat_sessions_is_active"), "chat_sessions", ["is_active"], unique=False)
    op.create_index(op.f("ix_chat_sessions_seller_id"), "chat_sessions", ["seller_id"], unique=False)
    op.create_index("ix_chat_sessions_buyer_active", "chat_sessions", ["buyer_id", "is_active"], unique=False)
    op.create_index("ix_chat_sessions_seller_active", "chat_sessions", ["seller_id", "is_active"], unique=False)

    # 5. chat_messages
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("chat_id", sa.Integer(), nullable=False),
        sa.Column("sender_id", sa.BigInteger(), nullable=False),
        sa.Column("content_type", sa.String(length=32), server_default="text", nullable=False),
        sa.Column("text_content", sa.Text(), nullable=True),
        sa.Column("file_id", sa.String(length=255), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["chat_id"], ["chat_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sender_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_chat_messages_chat_id"), "chat_messages", ["chat_id"], unique=False)
    op.create_index(op.f("ix_chat_messages_sender_id"), "chat_messages", ["sender_id"], unique=False)
    op.create_index(op.f("ix_chat_messages_sent_at"), "chat_messages", ["sent_at"], unique=False)

    # 6. reports
    op.create_table(
        "reports",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("reporter_id", sa.BigInteger(), nullable=False),
        sa.Column("violator_id", sa.BigInteger(), nullable=False),
        sa.Column("ad_id", sa.Integer(), nullable=True),
        sa.Column("chat_id", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["ad_id"], ["ads.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["chat_id"], ["chat_sessions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reporter_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["violator_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_reports_ad_id"), "reports", ["ad_id"], unique=False)
    op.create_index(op.f("ix_reports_chat_id"), "reports", ["chat_id"], unique=False)
    op.create_index(op.f("ix_reports_reporter_id"), "reports", ["reporter_id"], unique=False)
    op.create_index(op.f("ix_reports_status"), "reports", ["status"], unique=False)
    op.create_index(op.f("ix_reports_violator_id"), "reports", ["violator_id"], unique=False)

    # 7. star_transactions
    op.create_table(
        "star_transactions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("ad_id", sa.Integer(), nullable=False),
        sa.Column("tariff", sa.String(length=32), nullable=False),
        sa.Column("stars_amount", sa.Integer(), nullable=False),
        sa.Column("telegram_payment_charge_id", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["ad_id"], ["ads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_payment_charge_id"),
    )
    op.create_index(op.f("ix_star_transactions_ad_id"), "star_transactions", ["ad_id"], unique=False)
    op.create_index(op.f("ix_star_transactions_telegram_payment_charge_id"), "star_transactions", ["telegram_payment_charge_id"], unique=True)
    op.create_index(op.f("ix_star_transactions_user_id"), "star_transactions", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_table("star_transactions")
    op.drop_table("reports")
    op.drop_table("chat_messages")
    op.drop_table("chat_sessions")
    op.drop_table("search_subscriptions")
    op.drop_table("ads")
    op.drop_table("users")
