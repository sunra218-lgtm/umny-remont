"""Спринт 6: заявки на материалы, маржа закупок, оплата заказов, накладные, исход спора

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-27 14:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STAGE_STATUS = postgresql.ENUM(name="stage_status", create_type=False)


def upgrade() -> None:
    op.create_table(
        "material_requests",
        sa.Column("object_id", sa.Uuid(), nullable=False),
        sa.Column("stage_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("audio_key", sa.String(length=300), nullable=True),
        sa.Column("audio_type", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="new", nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "text IS NOT NULL OR audio_key IS NOT NULL",
            name=op.f("ck_material_requests_has_content"),
        ),
        sa.CheckConstraint(
            "status IN ('new', 'in_work', 'done')", name=op.f("ck_material_requests_status_valid")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_material_requests_created_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["object_id"], ["objects.id"], name=op.f("fk_material_requests_object_id_objects")
        ),
        sa.ForeignKeyConstraint(
            ["stage_id"], ["stages.id"], name=op.f("fk_material_requests_stage_id_stages")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_material_requests")),
    )
    op.create_index(
        op.f("ix_material_requests_object_id"), "material_requests", ["object_id"], unique=False
    )

    op.add_column(
        "purchase_orders",
        sa.Column("supplier_cost_kop", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.add_column("purchase_orders", sa.Column("comment", sa.Text(), nullable=True))
    op.add_column("purchase_orders", sa.Column("reject_reason", sa.Text(), nullable=True))
    op.add_column("purchase_orders", sa.Column("request_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_purchase_orders_request_id_material_requests",
        "purchase_orders",
        "material_requests",
        ["request_id"],
        ["id"],
    )

    op.add_column("payments", sa.Column("purchase_order_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_payments_purchase_order_id_purchase_orders",
        "payments",
        "purchase_orders",
        ["purchase_order_id"],
        ["id"],
    )
    op.create_index(
        op.f("ix_payments_purchase_order_id"), "payments", ["purchase_order_id"], unique=False
    )

    op.add_column("deliveries", sa.Column("invoice_key", sa.String(length=300), nullable=True))
    op.add_column("deliveries", sa.Column("comment", sa.Text(), nullable=True))

    op.add_column("disputes", sa.Column("stage_status_before", STAGE_STATUS, nullable=True))
    op.add_column("disputes", sa.Column("outcome", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("disputes", "outcome")
    op.drop_column("disputes", "stage_status_before")
    op.drop_column("deliveries", "comment")
    op.drop_column("deliveries", "invoice_key")
    op.drop_index(op.f("ix_payments_purchase_order_id"), table_name="payments")
    op.drop_constraint(
        "fk_payments_purchase_order_id_purchase_orders", "payments", type_="foreignkey"
    )
    op.drop_column("payments", "purchase_order_id")
    op.drop_constraint(
        "fk_purchase_orders_request_id_material_requests", "purchase_orders", type_="foreignkey"
    )
    op.drop_column("purchase_orders", "request_id")
    op.drop_column("purchase_orders", "reject_reason")
    op.drop_column("purchase_orders", "comment")
    op.drop_column("purchase_orders", "supplier_cost_kop")
    op.drop_index(op.f("ix_material_requests_object_id"), table_name="material_requests")
    op.drop_table("material_requests")
