"""Спринт 4: проверка фото (кто, когда, замечание)

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-27 13:10:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("media_reports", sa.Column("reviewed_by", sa.Uuid(), nullable=True))
    op.add_column(
        "media_reports", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("media_reports", sa.Column("review_comment", sa.Text(), nullable=True))
    op.create_foreign_key(
        "fk_media_reports_reviewed_by_users", "media_reports", "users", ["reviewed_by"], ["id"]
    )


def downgrade() -> None:
    op.drop_constraint("fk_media_reports_reviewed_by_users", "media_reports", type_="foreignkey")
    op.drop_column("media_reports", "review_comment")
    op.drop_column("media_reports", "reviewed_at")
    op.drop_column("media_reports", "reviewed_by")
