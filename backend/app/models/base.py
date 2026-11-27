import enum
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, MetaData, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.ids import uuid7

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


def pg_enum(enum_cls: type[enum.Enum], name: str, create_type: bool = True) -> Enum:
    """ENUM в PostgreSQL, хранит значения (а не имена) элементов Python-перечисления."""
    return Enum(
        enum_cls,
        name=name,
        create_type=create_type,
        values_callable=lambda e: [member.value for member in e],
        validate_strings=True,
    )


class UUIDPk:
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class SoftDelete:
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


def money() -> Mapped[int]:
    """Деньги: целые копейки (BIGINT), валюта RUB."""
    return mapped_column(BigInteger, nullable=False, default=0, server_default="0")
