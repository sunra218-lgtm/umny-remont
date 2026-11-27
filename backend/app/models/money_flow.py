"""Акты, подписи, платежи безопасной сделки, споры, закупки и поставки."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamps, UUIDPk, money, pg_enum
from app.models.enums import (
    ActStatus,
    DeliveryStatus,
    DisputeStatus,
    PaymentStatus,
    PaymentType,
    PurchaseOrderStatus,
    SignatureMethod,
    SignedDocumentType,
    StageStatus,
)


class Act(UUIDPk, Timestamps, Base):
    """Акт сдачи-приёмки этапа."""

    __tablename__ = "acts"
    __table_args__ = (UniqueConstraint("object_id", "number"),)

    object_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("objects.id"), nullable=False, index=True
    )
    stage_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stages.id"), nullable=False, index=True)
    number: Mapped[str] = mapped_column(String(50), nullable=False)
    work_kop: Mapped[int] = money()
    material_kop: Mapped[int] = money()
    inspection_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("inspections.id"))
    status: Mapped[ActStatus] = mapped_column(
        pg_enum(ActStatus, "act_status"), nullable=False, default=ActStatus.draft
    )
    reject_reason: Mapped[str | None] = mapped_column(Text)


class Signature(UUIDPk, Base):
    """Журнал подписаний простой электронной подписью по СМС-коду (63-ФЗ)."""

    __tablename__ = "signatures"

    document_type: Mapped[SignedDocumentType] = mapped_column(
        pg_enum(SignedDocumentType, "signed_document_type"), nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    method: Mapped[SignatureMethod] = mapped_column(
        pg_enum(SignatureMethod, "signature_method"), nullable=False
    )
    document_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    otp_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(String(500))


class Payment(UUIDPk, Timestamps, Base):
    """Операция безопасной сделки банка-партнёра."""

    __tablename__ = "payments"
    __table_args__ = (CheckConstraint("amount_kop > 0", name="amount_positive"),)

    object_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("objects.id"), nullable=False, index=True
    )
    stage_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("stages.id"), index=True)
    act_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("acts.id"), index=True)
    # Оплата заказа материалов (консьерж-закупка); у резерва этапа здесь пусто.
    purchase_order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("purchase_orders.id"), index=True
    )
    bank_op_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    type: Mapped[PaymentType] = mapped_column(pg_enum(PaymentType, "payment_type"), nullable=False)
    amount_kop: Mapped[int] = money()
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="RUB")
    split: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[PaymentStatus] = mapped_column(
        pg_enum(PaymentStatus, "payment_status"), nullable=False, default=PaymentStatus.created
    )
    bank_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class Dispute(UUIDPk, Timestamps, Base):
    """Спор по этапу; на время спора выплата замораживается."""

    __tablename__ = "disputes"

    object_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("objects.id"), nullable=False, index=True
    )
    stage_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("stages.id"), index=True)
    opened_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[DisputeStatus] = mapped_column(
        pg_enum(DisputeStatus, "dispute_status"), nullable=False, default=DisputeStatus.open
    )
    resolution: Mapped[str | None] = mapped_column(Text)
    frozen_payment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("payments.id"))
    # Статус этапа до заморозки: в него этап вернётся, если спор решён в пользу продолжения.
    stage_status_before: Mapped[StageStatus | None] = mapped_column(
        pg_enum(StageStatus, "stage_status", create_type=False)
    )
    outcome: Mapped[str | None] = mapped_column(String(20))  # continue | refund
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PurchaseOrder(UUIDPk, Timestamps, Base):
    """Корзина / заказ материалов (консьерж-закупки)."""

    __tablename__ = "purchase_orders"

    object_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("objects.id"), nullable=False, index=True
    )
    stage_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("stages.id"), index=True)
    supplier_org_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.id"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    status: Mapped[PurchaseOrderStatus] = mapped_column(
        pg_enum(PurchaseOrderStatus, "purchase_order_status"),
        nullable=False,
        default=PurchaseOrderStatus.draft,
    )
    total_kop: Mapped[int] = money()
    platform_fee_kop: Mapped[int] = money()
    delivery_slot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    proposed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_by_customer_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Сколько платформа платит поставщику; разница с total_kop — фактическая маржа (ТЗ 1.3).
    supplier_cost_kop: Mapped[int] = money()
    comment: Mapped[str | None] = mapped_column(Text)
    reject_reason: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("material_requests.id"))


class PurchaseOrderItem(UUIDPk, Base):
    __tablename__ = "purchase_order_items"
    __table_args__ = (CheckConstraint("qty > 0", name="qty_positive"),)

    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    estimate_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("estimate_items.id"))
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    sku: Mapped[str | None] = mapped_column(String(100))
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    unit_price_kop: Mapped[int] = money()


class Delivery(UUIDPk, Base):
    """Приёмка поставки на объекте: фото накладной и расхождения."""

    __tablename__ = "deliveries"

    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("purchase_orders.id"), nullable=False, index=True
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    invoice_media_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("media_reports.id"))
    discrepancies: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    invoice_key: Mapped[str | None] = mapped_column(String(300))
    comment: Mapped[str | None] = mapped_column(Text)
    status: Mapped[DeliveryStatus] = mapped_column(
        pg_enum(DeliveryStatus, "delivery_status"), nullable=False, default=DeliveryStatus.pending
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MaterialRequest(UUIDPk, Timestamps, Base):
    """Заявка прораба на материалы: текстом или голосом (аудиофайл)."""

    __tablename__ = "material_requests"

    object_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("objects.id"), nullable=False, index=True
    )
    stage_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("stages.id"))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    text: Mapped[str | None] = mapped_column(Text)
    audio_key: Mapped[str | None] = mapped_column(String(300))
    audio_type: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="new", server_default="new"
    )  # new | in_work | done
    __table_args__ = (
        CheckConstraint("text IS NOT NULL OR audio_key IS NOT NULL", name="has_content"),
        CheckConstraint("status IN ('new', 'in_work', 'done')", name="status_valid"),
    )
