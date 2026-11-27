"""Вопросы мастеров инженеру технадзора с фото

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-27 20:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "engineer_questions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("object_id", sa.UUID(), nullable=False),
        sa.Column("stage_id", sa.UUID(), nullable=True),
        sa.Column("author_id", sa.UUID(), nullable=False),
        sa.Column("step_key", sa.String(length=50), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("photo_key", sa.String(length=300), nullable=True),
        sa.Column("photo_type", sa.String(length=50), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="open", nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("answered_by", sa.UUID(), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["object_id"], ["objects.id"], name=op.f("fk_engineer_questions_object_id_objects")
        ),
        sa.ForeignKeyConstraint(
            ["stage_id"], ["stages.id"], name=op.f("fk_engineer_questions_stage_id_stages")
        ),
        sa.ForeignKeyConstraint(
            ["author_id"], ["users.id"], name=op.f("fk_engineer_questions_author_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["answered_by"], ["users.id"], name=op.f("fk_engineer_questions_answered_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_engineer_questions")),
    )
    op.create_index(
        op.f("ix_engineer_questions_object_id"), "engineer_questions", ["object_id"], unique=False
    )


def downgrade() -> None:
    op.drop_table("engineer_questions")
