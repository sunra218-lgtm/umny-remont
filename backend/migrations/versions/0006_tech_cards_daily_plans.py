"""Техкарты с допусками, пункты чек-листа из шагов техкарты, отметки технадзора, дневные планы

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-27 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "tech_cards",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("stage_type", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("norms", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("steps", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("materials", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_by", sa.UUID(), nullable=True),
        *_ts(),
        sa.ForeignKeyConstraint(
            ["verified_by"], ["users.id"], name=op.f("fk_tech_cards_verified_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tech_cards")),
        sa.UniqueConstraint("stage_type", name=op.f("uq_tech_cards_stage_type")),
    )
    op.add_column("checklist_items", sa.Column("step_key", sa.String(length=50), nullable=True))
    op.add_column(
        "inspections",
        sa.Column("checks", postgresql.JSONB(), server_default="[]", nullable=False),
    )
    op.create_table(
        "daily_plans",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("object_id", sa.UUID(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="planned", nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("report_note", sa.Text(), nullable=True),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.UUID(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        *_ts(),
        sa.ForeignKeyConstraint(
            ["object_id"], ["objects.id"], name=op.f("fk_daily_plans_object_id_objects")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_daily_plans_created_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"], ["users.id"], name=op.f("fk_daily_plans_reviewed_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_daily_plans")),
        sa.UniqueConstraint("object_id", "day", name="uq_daily_plan_day"),
    )
    op.create_index(op.f("ix_daily_plans_object_id"), "daily_plans", ["object_id"])
    op.create_table(
        "daily_plan_items",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("plan_id", sa.UUID(), nullable=False),
        sa.Column("seq", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("stage_id", sa.UUID(), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("done", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["daily_plans.id"],
            name=op.f("fk_daily_plan_items_plan_id_daily_plans"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["stage_id"], ["stages.id"], name=op.f("fk_daily_plan_items_stage_id_stages")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_daily_plan_items")),
    )
    op.create_index(op.f("ix_daily_plan_items_plan_id"), "daily_plan_items", ["plan_id"])


def downgrade() -> None:
    op.drop_table("daily_plan_items")
    op.drop_table("daily_plans")
    op.drop_column("inspections", "checks")
    op.drop_column("checklist_items", "step_key")
    op.drop_table("tech_cards")
