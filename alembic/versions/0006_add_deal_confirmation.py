"""add_deal_confirmation

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-17 08:00:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "chat_sessions" in tables:
        cols = [c["name"] for c in inspector.get_columns("chat_sessions")]
        if "buyer_confirmed_deal" not in cols:
            op.add_column(
                "chat_sessions",
                sa.Column("buyer_confirmed_deal", sa.Boolean(), nullable=True),
            )
        if "seller_confirmed_deal" not in cols:
            op.add_column(
                "chat_sessions",
                sa.Column("seller_confirmed_deal", sa.Boolean(), nullable=True),
            )
        if "deal_completed" not in cols:
            op.add_column(
                "chat_sessions",
                sa.Column(
                    "deal_completed",
                    sa.Boolean(),
                    server_default=sa.false(),
                    nullable=False,
                ),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "chat_sessions" in tables:
        cols = [c["name"] for c in inspector.get_columns("chat_sessions")]
        if "deal_completed" in cols:
            op.drop_column("chat_sessions", "deal_completed")
        if "seller_confirmed_deal" in cols:
            op.drop_column("chat_sessions", "seller_confirmed_deal")
        if "buyer_confirmed_deal" in cols:
            op.drop_column("chat_sessions", "buyer_confirmed_deal")

