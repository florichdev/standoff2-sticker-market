"""fix_sticker_count_zero

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-29 11:15:00.000000

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("check_sticker_count_range", "ads", type_="check")
    op.create_check_constraint(
        "check_sticker_count_range",
        "ads",
        "sticker_count >= 0 AND sticker_count <= 4",
    )


def downgrade() -> None:
    op.drop_constraint("check_sticker_count_range", "ads", type_="check")
    op.create_check_constraint(
        "check_sticker_count_range",
        "ads",
        "sticker_count >= 1 AND sticker_count <= 4",
    )
