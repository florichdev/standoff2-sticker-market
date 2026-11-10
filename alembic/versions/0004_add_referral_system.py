"""add_referral_system

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-03 16:35:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # 1. Add free_top_boosts to users table if not exists
    cols = [c["name"] for c in inspector.get_columns("users")]
    if "free_top_boosts" not in cols:
        op.add_column(
            "users",
            sa.Column(
                "free_top_boosts",
                sa.Integer(),
                server_default=sa.text("0"),
                nullable=False,
            ),
        )

    # 2. Create referrals table if not exists
    tables = inspector.get_table_names()
    if "referrals" not in tables:
        op.create_table(
            "referrals",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
            sa.Column("referrer_id", sa.BigInteger(), nullable=False),
            sa.Column("referred_id", sa.BigInteger(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["referrer_id"],
                ["users.id"],
                name=op.f("fk_referrals_referrer_id_users"),
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["referred_id"],
                ["users.id"],
                name=op.f("fk_referrals_referred_id_users"),
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_referrals")),
            sa.UniqueConstraint("referred_id", name=op.f("uq_referrals_referred_id")),
        )
        op.create_index(
            op.f("ix_referrals_referrer_id"), "referrals", ["referrer_id"], unique=False
        )
        op.create_index(
            op.f("ix_referrals_referred_id"), "referrals", ["referred_id"], unique=True
        )


def downgrade() -> None:
    op.drop_index(op.f("ix_referrals_referred_id"), table_name="referrals")
    op.drop_index(op.f("ix_referrals_referrer_id"), table_name="referrals")
    op.drop_table("referrals")
    op.drop_column("users", "free_top_boosts")

