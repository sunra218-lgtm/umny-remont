"""Начальная схема БД по ТЗ 2.3, раздел 4

Плюс для audit_logs: помесячные партиции и запрет UPDATE/DELETE триггером.

Revision ID: 0001
Revises:
Create Date: 2026-11-27 11:27:10.708854
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.UUID(), nullable=True),
        sa.Column("actor_id", sa.UUID(), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("old_val", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("new_val", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column("is_system", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.PrimaryKeyConstraint("id", "created_at", name=op.f("pk_audit_logs")),
        postgresql_partition_by="RANGE (created_at)",
    )
    op.create_table(
        "checklist_templates",
        sa.Column("stage_type", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_checklist_templates")),
    )
    op.create_index(
        op.f("ix_checklist_templates_stage_type"),
        "checklist_templates",
        ["stage_type"],
        unique=False,
    )
    op.create_table(
        "organizations",
        sa.Column(
            "type", sa.Enum("contractor", "supplier", "platform", name="org_type"), nullable=False
        ),
        sa.Column("inn", sa.String(length=12), nullable=False),
        sa.Column("ogrn", sa.String(length=15), nullable=True),
        sa.Column("kpp", sa.String(length=9), nullable=True),
        sa.Column("legal_name", sa.String(length=300), nullable=False),
        sa.Column("bank_bik", sa.String(length=9), nullable=True),
        sa.Column("bank_account", sa.String(length=20), nullable=True),
        sa.Column("rating", sa.Numeric(precision=3, scale=2), nullable=True),
        sa.Column("sla_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organizations")),
        sa.UniqueConstraint("inn", name=op.f("uq_organizations_inn")),
    )
    op.create_table(
        "users",
        sa.Column("phone", sa.String(length=16), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=True),
        sa.Column("full_name", sa.String(length=200), nullable=True),
        sa.Column("blocked_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("phone", name=op.f("uq_users_phone")),
    )
    op.create_table(
        "checklist_items",
        sa.Column("template_id", sa.UUID(), nullable=False),
        sa.Column("seq", sa.SmallInteger(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("required_angle", sa.String(length=200), nullable=True),
        sa.Column("photo_required", sa.Boolean(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["checklist_templates.id"],
            name=op.f("fk_checklist_items_template_id_checklist_templates"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_checklist_items")),
    )
    op.create_index(
        op.f("ix_checklist_items_template_id"), "checklist_items", ["template_id"], unique=False
    )
    op.create_table(
        "consents",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "type",
            sa.Enum("pd_processing", "photo_video", "marketing", name="consent_type"),
            nullable=False,
        ),
        sa.Column("text_version", sa.String(length=50), nullable=False),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_consents_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_consents")),
    )
    op.create_index(op.f("ix_consents_user_id"), "consents", ["user_id"], unique=False)
    op.create_table(
        "device_sessions",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Enum("web", "lite", name="session_kind"), nullable=False),
        sa.Column("refresh_token_hash", sa.String(length=64), nullable=False),
        sa.Column("device_key_hash", sa.String(length=64), nullable=True),
        sa.Column("device_public_key", sa.Text(), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_device_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_device_sessions")),
        sa.UniqueConstraint(
            "refresh_token_hash", name=op.f("uq_device_sessions_refresh_token_hash")
        ),
    )
    op.create_index(
        op.f("ix_device_sessions_user_id"), "device_sessions", ["user_id"], unique=False
    )
    op.create_table(
        "notifications",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "channel",
            sa.Enum("sms", "email", "vk", "max", name="notification_channel"),
            nullable=False,
        ),
        sa.Column("template", sa.String(length=100), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            sa.Enum("queued", "sent", "failed", name="notification_status"),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_notifications_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
    )
    op.create_index(op.f("ix_notifications_user_id"), "notifications", ["user_id"], unique=False)
    op.create_table(
        "objects",
        sa.Column("customer_id", sa.UUID(), nullable=False),
        sa.Column("contractor_org_id", sa.UUID(), nullable=True),
        sa.Column("address_fias", sa.String(length=36), nullable=True),
        sa.Column("address_text", sa.String(length=500), nullable=False),
        sa.Column("geo_lat", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("geo_lon", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("area_m2", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "survey",
                "estimating",
                "contracting",
                "active",
                "dispute",
                "completed",
                "cancelled",
                name="object_status",
            ),
            nullable=False,
        ),
        sa.Column("bank_deal_id", sa.String(length=100), nullable=True),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["contractor_org_id"],
            ["organizations.id"],
            name=op.f("fk_objects_contractor_org_id_organizations"),
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["users.id"], name=op.f("fk_objects_customer_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_objects")),
    )
    op.create_index(
        op.f("ix_objects_contractor_org_id"), "objects", ["contractor_org_id"], unique=False
    )
    op.create_index(op.f("ix_objects_customer_id"), "objects", ["customer_id"], unique=False)
    op.create_table(
        "org_members",
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "contractor_admin",
                "estimator",
                "foreman",
                "supplier_manager",
                "platform_admin",
                "procurement_manager",
                "inspector",
                name="org_role",
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_org_members_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_org_members_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_org_members")),
        sa.UniqueConstraint(
            "org_id", "user_id", "role", name=op.f("uq_org_members_org_id_user_id_role")
        ),
    )
    op.create_index(op.f("ix_org_members_org_id"), "org_members", ["org_id"], unique=False)
    op.create_index(op.f("ix_org_members_user_id"), "org_members", ["user_id"], unique=False)
    op.create_table(
        "signatures",
        sa.Column(
            "document_type",
            sa.Enum("contract", "estimate", "act", "inspection", name="signed_document_type"),
            nullable=False,
        ),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "method", sa.Enum("sms_pep", "gosklyuch", name="signature_method"), nullable=False
        ),
        sa.Column("document_hash", sa.String(length=64), nullable=False),
        sa.Column("otp_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_signatures_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_signatures")),
    )
    op.create_index(op.f("ix_signatures_document_id"), "signatures", ["document_id"], unique=False)
    op.create_table(
        "auth_links",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("object_id", sa.UUID(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_ip", postgresql.INET(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_auth_links_created_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["object_id"], ["objects.id"], name=op.f("fk_auth_links_object_id_objects")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_auth_links_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_links")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_auth_links_token_hash")),
    )
    op.create_index(op.f("ix_auth_links_user_id"), "auth_links", ["user_id"], unique=False)
    op.create_table(
        "documents",
        sa.Column("object_id", sa.UUID(), nullable=True),
        sa.Column(
            "type",
            sa.Enum(
                "contract",
                "estimate",
                "act",
                "passport",
                "inspection_report",
                "invoice",
                "other",
                name="document_type",
            ),
            nullable=False,
        ),
        sa.Column("s3_key", sa.String(length=500), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_documents_created_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["object_id"], ["objects.id"], name=op.f("fk_documents_object_id_objects")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
    )
    op.create_index(op.f("ix_documents_object_id"), "documents", ["object_id"], unique=False)
    op.create_table(
        "estimates",
        sa.Column("object_id", sa.UUID(), nullable=False),
        sa.Column("type", sa.Enum("preliminary", "full", name="estimate_type"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("total_work_kop", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("total_material_kop", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("reserve_kop", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_signature_id", sa.UUID(), nullable=True),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["approved_signature_id"],
            ["signatures.id"],
            name=op.f("fk_estimates_approved_signature_id_signatures"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_estimates_created_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["object_id"],
            ["objects.id"],
            name=op.f("fk_estimates_object_id_objects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_estimates")),
        sa.UniqueConstraint(
            "object_id", "type", "version", name=op.f("uq_estimates_object_id_type_version")
        ),
    )
    op.create_index(op.f("ix_estimates_object_id"), "estimates", ["object_id"], unique=False)
    op.create_table(
        "object_assignments",
        sa.Column("object_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "role",
            sa.Enum("foreman", "inspector", "procurement_manager", name="assignment_role"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["object_id"],
            ["objects.id"],
            name=op.f("fk_object_assignments_object_id_objects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_object_assignments_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_object_assignments")),
        sa.UniqueConstraint(
            "object_id",
            "user_id",
            "role",
            name=op.f("uq_object_assignments_object_id_user_id_role"),
        ),
    )
    op.create_index(
        op.f("ix_object_assignments_object_id"), "object_assignments", ["object_id"], unique=False
    )
    op.create_index(
        op.f("ix_object_assignments_user_id"), "object_assignments", ["user_id"], unique=False
    )
    op.create_table(
        "stages",
        sa.Column("object_id", sa.UUID(), nullable=False),
        sa.Column("seq", sa.SmallInteger(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("stage_type", sa.String(length=50), nullable=True),
        sa.Column("work_budget_kop", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("material_budget_kop", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("is_stop_point", sa.Boolean(), nullable=False),
        sa.Column("planned_start", sa.Date(), nullable=True),
        sa.Column("planned_end", sa.Date(), nullable=True),
        sa.Column("progress_pct", sa.SmallInteger(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "planned",
                "funding",
                "ready",
                "in_progress",
                "inspection",
                "acceptance",
                "accepted",
                "paid",
                "frozen",
                name="stage_status",
            ),
            nullable=False,
        ),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "progress_pct BETWEEN 0 AND 100", name=op.f("ck_stages_progress_pct_range")
        ),
        sa.ForeignKeyConstraint(
            ["object_id"],
            ["objects.id"],
            name=op.f("fk_stages_object_id_objects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stages")),
        sa.UniqueConstraint("object_id", "seq", name=op.f("uq_stages_object_id_seq")),
    )
    op.create_index(op.f("ix_stages_object_id"), "stages", ["object_id"], unique=False)
    op.create_table(
        "capture_challenges",
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("object_id", sa.UUID(), nullable=False),
        sa.Column("stage_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column(
            "issued_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["object_id"], ["objects.id"], name=op.f("fk_capture_challenges_object_id_objects")
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["device_sessions.id"],
            name=op.f("fk_capture_challenges_session_id_device_sessions"),
        ),
        sa.ForeignKeyConstraint(
            ["stage_id"], ["stages.id"], name=op.f("fk_capture_challenges_stage_id_stages")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_capture_challenges")),
    )
    op.create_index(
        op.f("ix_capture_challenges_session_id"), "capture_challenges", ["session_id"], unique=False
    )
    op.create_table(
        "estimate_items",
        sa.Column("estimate_id", sa.UUID(), nullable=False),
        sa.Column("stage_id", sa.UUID(), nullable=True),
        sa.Column("room", sa.String(length=100), nullable=True),
        sa.Column(
            "category", sa.Enum("work", "material", name="estimate_item_category"), nullable=False
        ),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=False),
        sa.Column("qty", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("unit_price_kop", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("sku_id", sa.String(length=100), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.CheckConstraint("qty >= 0", name=op.f("ck_estimate_items_qty_non_negative")),
        sa.ForeignKeyConstraint(
            ["estimate_id"],
            ["estimates.id"],
            name=op.f("fk_estimate_items_estimate_id_estimates"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["stage_id"], ["stages.id"], name=op.f("fk_estimate_items_stage_id_stages")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_estimate_items")),
    )
    op.create_index(
        op.f("ix_estimate_items_estimate_id"), "estimate_items", ["estimate_id"], unique=False
    )
    op.create_index(
        op.f("ix_estimate_items_stage_id"), "estimate_items", ["stage_id"], unique=False
    )
    op.create_table(
        "inspections",
        sa.Column("stage_id", sa.UUID(), nullable=False),
        sa.Column("inspector_id", sa.UUID(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "result",
            sa.Enum("passed", "failed", "remarks", name="inspection_result"),
            nullable=True,
        ),
        sa.Column("remarks", sa.Text(), nullable=True),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["inspector_id"], ["users.id"], name=op.f("fk_inspections_inspector_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["stage_id"], ["stages.id"], name=op.f("fk_inspections_stage_id_stages")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_inspections")),
    )
    op.create_index(op.f("ix_inspections_stage_id"), "inspections", ["stage_id"], unique=False)
    op.create_table(
        "purchase_orders",
        sa.Column("object_id", sa.UUID(), nullable=False),
        sa.Column("stage_id", sa.UUID(), nullable=True),
        sa.Column("supplier_org_id", sa.UUID(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "proposed",
                "confirmed",
                "ordered",
                "delivering",
                "delivered",
                "partially_delivered",
                "expired",
                "rejected",
                name="purchase_order_status",
            ),
            nullable=False,
        ),
        sa.Column("total_kop", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("platform_fee_kop", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("delivery_slot_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("proposed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_by_customer_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_purchase_orders_created_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["object_id"], ["objects.id"], name=op.f("fk_purchase_orders_object_id_objects")
        ),
        sa.ForeignKeyConstraint(
            ["stage_id"], ["stages.id"], name=op.f("fk_purchase_orders_stage_id_stages")
        ),
        sa.ForeignKeyConstraint(
            ["supplier_org_id"],
            ["organizations.id"],
            name=op.f("fk_purchase_orders_supplier_org_id_organizations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchase_orders")),
    )
    op.create_index(
        op.f("ix_purchase_orders_object_id"), "purchase_orders", ["object_id"], unique=False
    )
    op.create_index(
        op.f("ix_purchase_orders_stage_id"), "purchase_orders", ["stage_id"], unique=False
    )
    op.create_table(
        "service_orders",
        sa.Column("object_id", sa.UUID(), nullable=False),
        sa.Column(
            "type", sa.Enum("acceptance", "survey", name="service_order_type"), nullable=False
        ),
        sa.Column("inspector_id", sa.UUID(), nullable=True),
        sa.Column("slot_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("price_kop", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "created",
                "scheduled",
                "paid",
                "completed",
                "cancelled",
                name="service_order_status",
            ),
            nullable=False,
        ),
        sa.Column("report_document_id", sa.UUID(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["inspector_id"], ["users.id"], name=op.f("fk_service_orders_inspector_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["object_id"], ["objects.id"], name=op.f("fk_service_orders_object_id_objects")
        ),
        sa.ForeignKeyConstraint(
            ["report_document_id"],
            ["documents.id"],
            name=op.f("fk_service_orders_report_document_id_documents"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_service_orders")),
    )
    op.create_index(
        op.f("ix_service_orders_object_id"), "service_orders", ["object_id"], unique=False
    )
    op.create_table(
        "acts",
        sa.Column("object_id", sa.UUID(), nullable=False),
        sa.Column("stage_id", sa.UUID(), nullable=False),
        sa.Column("number", sa.String(length=50), nullable=False),
        sa.Column("work_kop", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("material_kop", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("inspection_id", sa.UUID(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "sent",
                "signed_contractor",
                "signed_customer",
                "released",
                "rejected",
                name="act_status",
            ),
            nullable=False,
        ),
        sa.Column("reject_reason", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["inspection_id"], ["inspections.id"], name=op.f("fk_acts_inspection_id_inspections")
        ),
        sa.ForeignKeyConstraint(
            ["object_id"], ["objects.id"], name=op.f("fk_acts_object_id_objects")
        ),
        sa.ForeignKeyConstraint(["stage_id"], ["stages.id"], name=op.f("fk_acts_stage_id_stages")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_acts")),
        sa.UniqueConstraint("object_id", "number", name=op.f("uq_acts_object_id_number")),
    )
    op.create_index(op.f("ix_acts_object_id"), "acts", ["object_id"], unique=False)
    op.create_index(op.f("ix_acts_stage_id"), "acts", ["stage_id"], unique=False)
    op.create_table(
        "media_reports",
        sa.Column("object_id", sa.UUID(), nullable=False),
        sa.Column("stage_id", sa.UUID(), nullable=True),
        sa.Column("checklist_item_id", sa.UUID(), nullable=True),
        sa.Column("author_id", sa.UUID(), nullable=False),
        sa.Column("media_type", sa.Enum("photo", "video", name="media_type"), nullable=False),
        sa.Column("s3_key", sa.String(length=500), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("phash", sa.BigInteger(), nullable=True),
        sa.Column("captured_client_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "received_server_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("challenge_id", sa.UUID(), nullable=True),
        sa.Column("geo_lat", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("geo_lon", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("geo_accuracy_m", sa.Integer(), nullable=True),
        sa.Column("fraud_flags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "review_status",
            sa.Enum("pending", "approved", "rejected", name="review_status"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["author_id"], ["users.id"], name=op.f("fk_media_reports_author_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["challenge_id"],
            ["capture_challenges.id"],
            name=op.f("fk_media_reports_challenge_id_capture_challenges"),
        ),
        sa.ForeignKeyConstraint(
            ["checklist_item_id"],
            ["checklist_items.id"],
            name=op.f("fk_media_reports_checklist_item_id_checklist_items"),
        ),
        sa.ForeignKeyConstraint(
            ["object_id"], ["objects.id"], name=op.f("fk_media_reports_object_id_objects")
        ),
        sa.ForeignKeyConstraint(
            ["stage_id"], ["stages.id"], name=op.f("fk_media_reports_stage_id_stages")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_media_reports")),
    )
    op.create_index(
        op.f("ix_media_reports_object_id"), "media_reports", ["object_id"], unique=False
    )
    op.create_index(op.f("ix_media_reports_phash"), "media_reports", ["phash"], unique=False)
    op.create_index(op.f("ix_media_reports_sha256"), "media_reports", ["sha256"], unique=False)
    op.create_index(op.f("ix_media_reports_stage_id"), "media_reports", ["stage_id"], unique=False)
    op.create_table(
        "purchase_order_items",
        sa.Column("order_id", sa.UUID(), nullable=False),
        sa.Column("estimate_item_id", sa.UUID(), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("sku", sa.String(length=100), nullable=True),
        sa.Column("qty", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("unit_price_kop", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.CheckConstraint("qty > 0", name=op.f("ck_purchase_order_items_qty_positive")),
        sa.ForeignKeyConstraint(
            ["estimate_item_id"],
            ["estimate_items.id"],
            name=op.f("fk_purchase_order_items_estimate_item_id_estimate_items"),
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["purchase_orders.id"],
            name=op.f("fk_purchase_order_items_order_id_purchase_orders"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchase_order_items")),
    )
    op.create_index(
        op.f("ix_purchase_order_items_order_id"), "purchase_order_items", ["order_id"], unique=False
    )
    op.create_table(
        "deliveries",
        sa.Column("order_id", sa.UUID(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_by", sa.UUID(), nullable=True),
        sa.Column("invoice_media_id", sa.UUID(), nullable=True),
        sa.Column("discrepancies", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "accepted",
                "accepted_with_discrepancies",
                "rejected",
                name="delivery_status",
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["invoice_media_id"],
            ["media_reports.id"],
            name=op.f("fk_deliveries_invoice_media_id_media_reports"),
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["purchase_orders.id"],
            name=op.f("fk_deliveries_order_id_purchase_orders"),
        ),
        sa.ForeignKeyConstraint(
            ["received_by"], ["users.id"], name=op.f("fk_deliveries_received_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deliveries")),
    )
    op.create_index(op.f("ix_deliveries_order_id"), "deliveries", ["order_id"], unique=False)
    op.create_table(
        "payments",
        sa.Column("object_id", sa.UUID(), nullable=False),
        sa.Column("stage_id", sa.UUID(), nullable=True),
        sa.Column("act_id", sa.UUID(), nullable=True),
        sa.Column("bank_op_id", sa.String(length=100), nullable=True),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column(
            "type",
            sa.Enum("hold", "release", "freeze", "unfreeze", "refund", name="payment_type"),
            nullable=False,
        ),
        sa.Column("amount_kop", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("split", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "created",
                "pending_funding",
                "held",
                "release_requested",
                "released",
                "frozen",
                "refunded",
                "failed",
                name="payment_status",
            ),
            nullable=False,
        ),
        sa.Column("bank_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
        sa.CheckConstraint("amount_kop > 0", name=op.f("ck_payments_amount_positive")),
        sa.ForeignKeyConstraint(["act_id"], ["acts.id"], name=op.f("fk_payments_act_id_acts")),
        sa.ForeignKeyConstraint(
            ["object_id"], ["objects.id"], name=op.f("fk_payments_object_id_objects")
        ),
        sa.ForeignKeyConstraint(
            ["stage_id"], ["stages.id"], name=op.f("fk_payments_stage_id_stages")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payments")),
        sa.UniqueConstraint("bank_op_id", name=op.f("uq_payments_bank_op_id")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_payments_idempotency_key")),
    )
    op.create_index(op.f("ix_payments_act_id"), "payments", ["act_id"], unique=False)
    op.create_index(op.f("ix_payments_object_id"), "payments", ["object_id"], unique=False)
    op.create_index(op.f("ix_payments_stage_id"), "payments", ["stage_id"], unique=False)
    op.create_table(
        "disputes",
        sa.Column("object_id", sa.UUID(), nullable=False),
        sa.Column("stage_id", sa.UUID(), nullable=True),
        sa.Column("opened_by", sa.UUID(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("open", "in_review", "resolved", "rejected", name="dispute_status"),
            nullable=False,
        ),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column("frozen_payment_id", sa.UUID(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["frozen_payment_id"],
            ["payments.id"],
            name=op.f("fk_disputes_frozen_payment_id_payments"),
        ),
        sa.ForeignKeyConstraint(
            ["object_id"], ["objects.id"], name=op.f("fk_disputes_object_id_objects")
        ),
        sa.ForeignKeyConstraint(
            ["opened_by"], ["users.id"], name=op.f("fk_disputes_opened_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["stage_id"], ["stages.id"], name=op.f("fk_disputes_stage_id_stages")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_disputes")),
    )
    op.create_index(op.f("ix_disputes_object_id"), "disputes", ["object_id"], unique=False)
    op.create_index(op.f("ix_disputes_stage_id"), "disputes", ["stage_id"], unique=False)

    _audit_logs_setup()


def downgrade() -> None:
    op.drop_index(op.f("ix_disputes_stage_id"), table_name="disputes")
    op.drop_index(op.f("ix_disputes_object_id"), table_name="disputes")
    op.drop_table("disputes")
    op.drop_index(op.f("ix_payments_stage_id"), table_name="payments")
    op.drop_index(op.f("ix_payments_object_id"), table_name="payments")
    op.drop_index(op.f("ix_payments_act_id"), table_name="payments")
    op.drop_table("payments")
    op.drop_index(op.f("ix_deliveries_order_id"), table_name="deliveries")
    op.drop_table("deliveries")
    op.drop_index(op.f("ix_purchase_order_items_order_id"), table_name="purchase_order_items")
    op.drop_table("purchase_order_items")
    op.drop_index(op.f("ix_media_reports_stage_id"), table_name="media_reports")
    op.drop_index(op.f("ix_media_reports_sha256"), table_name="media_reports")
    op.drop_index(op.f("ix_media_reports_phash"), table_name="media_reports")
    op.drop_index(op.f("ix_media_reports_object_id"), table_name="media_reports")
    op.drop_table("media_reports")
    op.drop_index(op.f("ix_acts_stage_id"), table_name="acts")
    op.drop_index(op.f("ix_acts_object_id"), table_name="acts")
    op.drop_table("acts")
    op.drop_index(op.f("ix_service_orders_object_id"), table_name="service_orders")
    op.drop_table("service_orders")
    op.drop_index(op.f("ix_purchase_orders_stage_id"), table_name="purchase_orders")
    op.drop_index(op.f("ix_purchase_orders_object_id"), table_name="purchase_orders")
    op.drop_table("purchase_orders")
    op.drop_index(op.f("ix_inspections_stage_id"), table_name="inspections")
    op.drop_table("inspections")
    op.drop_index(op.f("ix_estimate_items_stage_id"), table_name="estimate_items")
    op.drop_index(op.f("ix_estimate_items_estimate_id"), table_name="estimate_items")
    op.drop_table("estimate_items")
    op.drop_index(op.f("ix_capture_challenges_session_id"), table_name="capture_challenges")
    op.drop_table("capture_challenges")
    op.drop_index(op.f("ix_stages_object_id"), table_name="stages")
    op.drop_table("stages")
    op.drop_index(op.f("ix_object_assignments_user_id"), table_name="object_assignments")
    op.drop_index(op.f("ix_object_assignments_object_id"), table_name="object_assignments")
    op.drop_table("object_assignments")
    op.drop_index(op.f("ix_estimates_object_id"), table_name="estimates")
    op.drop_table("estimates")
    op.drop_index(op.f("ix_documents_object_id"), table_name="documents")
    op.drop_table("documents")
    op.drop_index(op.f("ix_auth_links_user_id"), table_name="auth_links")
    op.drop_table("auth_links")
    op.drop_index(op.f("ix_signatures_document_id"), table_name="signatures")
    op.drop_table("signatures")
    op.drop_index(op.f("ix_org_members_user_id"), table_name="org_members")
    op.drop_index(op.f("ix_org_members_org_id"), table_name="org_members")
    op.drop_table("org_members")
    op.drop_index(op.f("ix_objects_customer_id"), table_name="objects")
    op.drop_index(op.f("ix_objects_contractor_org_id"), table_name="objects")
    op.drop_table("objects")
    op.drop_index(op.f("ix_notifications_user_id"), table_name="notifications")
    op.drop_table("notifications")
    op.drop_index(op.f("ix_device_sessions_user_id"), table_name="device_sessions")
    op.drop_table("device_sessions")
    op.drop_index(op.f("ix_consents_user_id"), table_name="consents")
    op.drop_table("consents")
    op.drop_index(op.f("ix_checklist_items_template_id"), table_name="checklist_items")
    op.drop_table("checklist_items")
    op.drop_table("users")
    op.drop_table("organizations")
    op.drop_index(op.f("ix_checklist_templates_stage_type"), table_name="checklist_templates")
    op.drop_table("checklist_templates")
    op.drop_table("audit_logs")
    op.execute("DROP FUNCTION IF EXISTS audit_logs_forbid_change() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS ensure_audit_partition(date)")
    for enum_name in ENUM_NAMES:
        op.execute(f'DROP TYPE IF EXISTS "{enum_name}"')


ENUM_NAMES = [
    "act_status",
    "assignment_role",
    "consent_type",
    "delivery_status",
    "dispute_status",
    "document_type",
    "estimate_item_category",
    "estimate_type",
    "inspection_result",
    "media_type",
    "notification_channel",
    "notification_status",
    "object_status",
    "org_role",
    "org_type",
    "payment_status",
    "payment_type",
    "purchase_order_status",
    "review_status",
    "service_order_status",
    "service_order_type",
    "session_kind",
    "signature_method",
    "signed_document_type",
    "stage_status",
]


def _audit_logs_setup() -> None:
    # Партиция по умолчанию ловит записи, для месяца которых партиция ещё не создана.
    op.execute("CREATE TABLE audit_logs_default PARTITION OF audit_logs DEFAULT")
    op.execute(
        """
        CREATE FUNCTION ensure_audit_partition(month_start date) RETURNS void
        LANGUAGE plpgsql AS $$
        DECLARE
            start_d date := date_trunc('month', month_start)::date;
            end_d date := (date_trunc('month', month_start) + interval '1 month')::date;
            part_name text := 'audit_logs_' || to_char(start_d, 'YYYY_MM');
        BEGIN
            IF to_regclass(part_name) IS NULL THEN
                EXECUTE format(
                    'CREATE TABLE %I PARTITION OF audit_logs FOR VALUES FROM (%L) TO (%L)',
                    part_name, start_d, end_d
                );
            END IF;
        END $$;
        """
    )
    op.execute("SELECT ensure_audit_partition(now()::date)")
    op.execute("SELECT ensure_audit_partition((now() + interval '1 month')::date)")
    op.execute("CREATE INDEX ix_audit_logs_entity ON audit_logs (entity_type, entity_id)")
    op.execute("CREATE INDEX ix_audit_logs_actor ON audit_logs (actor_id)")
    # Журнал только дописывается: изменение и удаление записей запрещены на уровне БД.
    op.execute(
        """
        CREATE FUNCTION audit_logs_forbid_change() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_logs is append-only';
        END $$;
        """
    )
    op.execute(
        "CREATE TRIGGER audit_logs_append_only BEFORE UPDATE OR DELETE ON audit_logs "
        "FOR EACH ROW EXECUTE FUNCTION audit_logs_forbid_change()"
    )
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON audit_logs FROM PUBLIC")
