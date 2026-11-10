"""add_ad_broadcast_messages

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-08 09:50:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "ad_broadcast_messages" not in tables:
        op.create_table(
            "ad_broadcast_messages",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
            sa.Column(
                "ad_id",
                sa.Integer(),
                sa.ForeignKey("ads.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("chat_id", sa.BigInteger(), nullable=False),
            sa.Column("message_id", sa.Integer(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
        op.create_index(
            "ix_ad_broadcast_messages_ad_id",
            "ad_broadcast_messages",
            ["ad_id"],
            unique=False,
        )
        op.create_index(
            "ix_ad_broadcast_messages_chat_id",
            "ad_broadcast_messages",
            ["chat_id"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()
    if "ad_broadcast_messages" in tables:
        op.drop_table("ad_broadcast_messages")

