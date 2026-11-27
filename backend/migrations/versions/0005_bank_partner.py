"""Спринт 8: код получателя выплат в банке у организации

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-27 16:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "organizations", sa.Column("bank_partner_id", sa.String(length=64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("organizations", "bank_partner_id")
