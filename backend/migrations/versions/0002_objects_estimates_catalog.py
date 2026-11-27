"""Спринт 2: справочник расценок, версии смет, заказ выездов

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-27 11:48:20.078222
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "price_items",
        sa.Column(
            "category",
            postgresql.ENUM("work", "material", name="estimate_item_category", create_type=False),
            nullable=False,
        ),
        sa.Column("stage_type", sa.String(length=50), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=False),
        sa.Column("unit_price_kop", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("sku", sa.String(length=100), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_price_items")),
    )
    op.create_index(op.f("ix_price_items_stage_type"), "price_items", ["stage_type"], unique=False)
    op.add_column("estimate_items", sa.Column("price_item_id", sa.UUID(), nullable=True))
    op.add_column(
        "estimate_items", sa.Column("seq", sa.Integer(), server_default="0", nullable=False)
    )
    op.create_foreign_key(
        op.f("fk_estimate_items_price_item_id_price_items"),
        "estimate_items",
        "price_items",
        ["price_item_id"],
        ["id"],
    )
    op.add_column("estimates", sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("service_orders", sa.Column("created_by", sa.UUID(), nullable=True))
    op.add_column("service_orders", sa.Column("customer_comment", sa.Text(), nullable=True))
    op.add_column("service_orders", sa.Column("result_notes", sa.Text(), nullable=True))
    op.add_column(
        "service_orders", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_foreign_key(
        op.f("fk_service_orders_created_by_users"),
        "service_orders",
        "users",
        ["created_by"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_service_orders_created_by_users"), "service_orders", type_="foreignkey"
    )
    op.drop_column("service_orders", "completed_at")
    op.drop_column("service_orders", "result_notes")
    op.drop_column("service_orders", "customer_comment")
    op.drop_column("service_orders", "created_by")
    op.drop_column("estimates", "submitted_at")
    op.drop_constraint(
        op.f("fk_estimate_items_price_item_id_price_items"), "estimate_items", type_="foreignkey"
    )
    op.drop_column("estimate_items", "seq")
    op.drop_column("estimate_items", "price_item_id")
    op.drop_index(op.f("ix_price_items_stage_type"), table_name="price_items")
    op.drop_table("price_items")
