"""Объекты, назначения, выезды, сметы, этапы, чек-листы, проверки, фотоотчёты, документы."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, SoftDelete, Timestamps, UUIDPk, money, pg_enum
from app.models.enums import (
    AssignmentRole,
    DocumentType,
    EstimateItemCategory,
    EstimateType,
    InspectionResult,
    MediaType,
    ObjectStatus,
    ReviewStatus,
    ServiceOrderStatus,
    ServiceOrderType,
    StageStatus,
)


class RepairObject(UUIDPk, Timestamps, SoftDelete, Base):
    """Объект ремонта (квартира)."""

    __tablename__ = "objects"

    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    contractor_org_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    address_fias: Mapped[str | None] = mapped_column(String(36))
    address_text: Mapped[str] = mapped_column(String(500), nullable=False)
    geo_lat: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    geo_lon: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    area_m2: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    status: Mapped[ObjectStatus] = mapped_column(
        pg_enum(ObjectStatus, "object_status"), nullable=False, default=ObjectStatus.draft
    )
    bank_deal_id: Mapped[str | None] = mapped_column(String(100))


class ObjectAssignment(UUIDPk, Base):
    __tablename__ = "object_assignments"
    __table_args__ = (UniqueConstraint("object_id", "user_id", "role"),)

    object_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("objects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    role: Mapped[AssignmentRole] = mapped_column(
        pg_enum(AssignmentRole, "assignment_role"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Document(UUIDPk, SoftDelete, Base):
    """Договоры, акты, паспорт объекта (PDF) в объектном хранилище."""

    __tablename__ = "documents"

    object_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("objects.id"), index=True)
    type: Mapped[DocumentType] = mapped_column(
        pg_enum(DocumentType, "document_type"), nullable=False
    )
    s3_key: Mapped[str] = mapped_column(String(500), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ServiceOrder(UUIDPk, Timestamps, Base):
    """Платные выезды: «Умная приёмка» и замер."""

    __tablename__ = "service_orders"

    object_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("objects.id"), nullable=False, index=True
    )
    type: Mapped[ServiceOrderType] = mapped_column(
        pg_enum(ServiceOrderType, "service_order_type"), nullable=False
    )
    inspector_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    slot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    price_kop: Mapped[int] = money()
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[ServiceOrderStatus] = mapped_column(
        pg_enum(ServiceOrderStatus, "service_order_status"),
        nullable=False,
        default=ServiceOrderStatus.created,
    )
    report_document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    customer_comment: Mapped[str | None] = mapped_column(Text)
    result_notes: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Stage(UUIDPk, Timestamps, SoftDelete, Base):
    __tablename__ = "stages"
    __table_args__ = (
        UniqueConstraint("object_id", "seq"),
        CheckConstraint("progress_pct BETWEEN 0 AND 100", name="progress_pct_range"),
    )

    object_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("objects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    stage_type: Mapped[str | None] = mapped_column(String(50))
    work_budget_kop: Mapped[int] = money()
    material_budget_kop: Mapped[int] = money()
    is_stop_point: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    planned_start: Mapped[date | None] = mapped_column(Date)
    planned_end: Mapped[date | None] = mapped_column(Date)
    progress_pct: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    status: Mapped[StageStatus] = mapped_column(
        pg_enum(StageStatus, "stage_status"), nullable=False, default=StageStatus.planned
    )


class Estimate(UUIDPk, Timestamps, SoftDelete, Base):
    """Версии смет: предварительная и полная."""

    __tablename__ = "estimates"
    __table_args__ = (UniqueConstraint("object_id", "type", "version"),)

    object_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("objects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[EstimateType] = mapped_column(
        pg_enum(EstimateType, "estimate_type"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    total_work_kop: Mapped[int] = money()
    total_material_kop: Mapped[int] = money()
    reserve_kop: Mapped[int] = money()
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    # Сметчик отправил версию заказчику; после этого позиции не меняются.
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_signature_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("signatures.id"))


class PriceItem(UUIDPk, Timestamps, Base):
    """Справочник расценок: шаблоны работ и материалов, из которых собирается смета."""

    __tablename__ = "price_items"

    category: Mapped[EstimateItemCategory] = mapped_column(
        pg_enum(EstimateItemCategory, "estimate_item_category", create_type=False), nullable=False
    )
    stage_type: Mapped[str | None] = mapped_column(String(50), index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    unit_price_kop: Mapped[int] = money()
    sku: Mapped[str | None] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )


class EstimateItem(UUIDPk, Base):
    __tablename__ = "estimate_items"
    __table_args__ = (CheckConstraint("qty >= 0", name="qty_non_negative"),)

    estimate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("estimates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("stages.id"), index=True)
    room: Mapped[str | None] = mapped_column(String(100))
    category: Mapped[EstimateItemCategory] = mapped_column(
        pg_enum(EstimateItemCategory, "estimate_item_category"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    unit_price_kop: Mapped[int] = money()
    sku_id: Mapped[str | None] = mapped_column(String(100))
    price_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("price_items.id"))
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class ChecklistTemplate(UUIDPk, Timestamps, Base):
    """Шаблон чек-листа ракурсов и стоп-точек для типа этапа."""

    __tablename__ = "checklist_templates"

    stage_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)


class ChecklistItem(UUIDPk, Base):
    __tablename__ = "checklist_items"

    template_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("checklist_templates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    required_angle: Mapped[str | None] = mapped_column(String(200))
    photo_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Inspection(UUIDPk, Timestamps, Base):
    """Очная проверка стоп-точки инженером технадзора."""

    __tablename__ = "inspections"

    stage_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stages.id"), nullable=False, index=True)
    inspector_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[InspectionResult | None] = mapped_column(
        pg_enum(InspectionResult, "inspection_result")
    )
    remarks: Mapped[str | None] = mapped_column(Text)
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CaptureChallenge(UUIDPk, Base):
    """Серверный код съёмки для антифрода фотоотчётов (живёт 10 минут)."""

    __tablename__ = "capture_challenges"

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("device_sessions.id"), nullable=False, index=True
    )
    object_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("objects.id"), nullable=False)
    stage_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stages.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MediaReport(UUIDPk, Base):
    """Фото/видео отчёта. Неизменяемы после загрузки (кроме статуса проверки)."""

    __tablename__ = "media_reports"

    object_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("objects.id"), nullable=False, index=True
    )
    stage_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("stages.id"), index=True)
    checklist_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("checklist_items.id"))
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    media_type: Mapped[MediaType] = mapped_column(
        pg_enum(MediaType, "media_type"), nullable=False, default=MediaType.photo
    )
    s3_key: Mapped[str] = mapped_column(String(500), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    phash: Mapped[int | None] = mapped_column(BigInteger, index=True)
    captured_client_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_server_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    challenge_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("capture_challenges.id"))
    geo_lat: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    geo_lon: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    geo_accuracy_m: Mapped[int | None] = mapped_column(Integer)
    fraud_flags: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    review_status: Mapped[ReviewStatus] = mapped_column(
        pg_enum(ReviewStatus, "review_status"), nullable=False, default=ReviewStatus.pending
    )
