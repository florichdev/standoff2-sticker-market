"""add_user_is_active

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-29 16:50:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = [c["name"] for c in inspector.get_columns("users")]
    if "is_active" not in cols:
        op.add_column(
            "users",
            sa.Column(
                "is_active",
                sa.Boolean(),
                server_default=sa.text("true"),
                nullable=False,
            ),
        )
    indexes = [ix["name"] for ix in inspector.get_indexes("users")]
    if "ix_users_is_active" not in indexes:
        op.create_index(op.f("ix_users_is_active"), "users", ["is_active"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_users_is_active"), table_name="users")
    op.drop_column("users", "is_active")
