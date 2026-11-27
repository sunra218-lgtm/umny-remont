"""Люди, организации, роли, сессии, согласия, уведомления."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDelete, Timestamps, UUIDPk, pg_enum
from app.models.enums import (
    ConsentType,
    NotificationChannel,
    NotificationStatus,
    OrgRole,
    OrgType,
    SessionKind,
)


class User(UUIDPk, Timestamps, SoftDelete, Base):
    """Единый реестр людей. Роль задаётся через членство и назначения, а не одним полем."""

    __tablename__ = "users"

    phone: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(254))
    full_name: Mapped[str | None] = mapped_column(String(200))
    blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    memberships: Mapped[list["OrgMember"]] = relationship(back_populates="user", lazy="raise")


class Organization(UUIDPk, Timestamps, SoftDelete, Base):
    __tablename__ = "organizations"

    type: Mapped[OrgType] = mapped_column(pg_enum(OrgType, "org_type"), nullable=False)
    inn: Mapped[str] = mapped_column(String(12), unique=True, nullable=False)
    ogrn: Mapped[str | None] = mapped_column(String(15))
    kpp: Mapped[str | None] = mapped_column(String(9))
    legal_name: Mapped[str] = mapped_column(String(300), nullable=False)
    bank_bik: Mapped[str | None] = mapped_column(String(9))
    bank_account: Mapped[str | None] = mapped_column(String(20))
    # Код получателя выплат в банке безопасной сделки (у Т-Банка — PartnerId).
    bank_partner_id: Mapped[str | None] = mapped_column(String(64))
    rating: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    sla_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    members: Mapped[list["OrgMember"]] = relationship(back_populates="org", lazy="raise")


class OrgMember(UUIDPk, Base):
    __tablename__ = "org_members"
    __table_args__ = (UniqueConstraint("org_id", "user_id", "role"),)

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[OrgRole] = mapped_column(pg_enum(OrgRole, "org_role"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    org: Mapped[Organization] = relationship(back_populates="members", lazy="raise")
    user: Mapped[User] = relationship(back_populates="memberships", lazy="raise")


class AuthLink(UUIDPk, Base):
    """Одноразовые ссылки входа для бригад (TTL 30 мин)."""

    __tablename__ = "auth_links"

    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    object_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("objects.id"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_ip: Mapped[str | None] = mapped_column(INET)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DeviceSession(UUIDPk, Base):
    """Сессия входа на устройстве: веб-кабинет или Lite Web. Отзывается в любой момент."""

    __tablename__ = "device_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[SessionKind] = mapped_column(pg_enum(SessionKind, "session_kind"), nullable=False)
    refresh_token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # Публичный ключ устройства (WebCrypto) для Lite Web — спринт 3.
    device_key_hash: Mapped[str | None] = mapped_column(String(64))
    device_public_key: Mapped[str | None] = mapped_column(Text)
    user_agent: Mapped[str | None] = mapped_column(String(500))
    ip: Mapped[str | None] = mapped_column(INET)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Consent(UUIDPk, Base):
    """Согласия по 152-ФЗ. Версия текста хранится, отзыв — отдельной отметкой."""

    __tablename__ = "consents"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[ConsentType] = mapped_column(pg_enum(ConsentType, "consent_type"), nullable=False)
    text_version: Mapped[str] = mapped_column(String(50), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip: Mapped[str | None] = mapped_column(INET)


class Notification(UUIDPk, Base):
    __tablename__ = "notifications"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel: Mapped[NotificationChannel] = mapped_column(
        pg_enum(NotificationChannel, "notification_channel"), nullable=False
    )
    template: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[NotificationStatus] = mapped_column(
        pg_enum(NotificationStatus, "notification_status"),
        nullable=False,
        default=NotificationStatus.queued,
    )
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    """Append-only журнал. UPDATE/DELETE запрещены триггером в БД, таблица партиционирована
    по месяцам (см. миграцию)."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_entity", "entity_type", "entity_id"),
        Index("ix_audit_logs_actor", "actor_id"),
        {"postgresql_partition_by": "RANGE (created_at)"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, server_default=func.now(), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    old_val: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    new_val: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ip: Mapped[str | None] = mapped_column(INET)
    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
