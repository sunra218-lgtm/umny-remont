"""Маркетплейс подрядчиков: профили с портфолио, заявки заказчиков, отклики, отзывы.

Работает рядом с подбором подрядчика платформой: на каждом объекте выбран один режим
(``RepairObject.match_mode``), чтобы в пилоте сравнить, какой лучше.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamps, UUIDPk


class ContractorProfile(UUIDPk, Timestamps, Base):
    """Витрина подрядчика: строительная компания или бригада (ИП, самозанятые)."""

    __tablename__ = "contractor_profiles"

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    # company — строительная компания, brigade — бригада или частный мастер.
    kind: Mapped[str] = mapped_column(
        String(10), nullable=False, default="company", server_default="company"
    )
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    about: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(String(100))
    # Виды работ — коды этапов (как в техкартах).
    specializations: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    team_size: Mapped[int | None] = mapped_column(SmallInteger)
    since_year: Mapped[int | None] = mapped_column(SmallInteger)
    # Берёт ли заявки с маркетплейса.
    accepts_tasks: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )


class PortfolioPhoto(UUIDPk, Base):
    __tablename__ = "portfolio_photos"

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    storage_key: Mapped[str] = mapped_column(String(300), nullable=False)
    content_type: Mapped[str] = mapped_column(String(50), nullable=False)
    caption: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MarketTask(UUIDPk, Timestamps, Base):
    """Заявка заказчика на ремонт: подрядчики видят её без точного адреса и откликаются."""

    __tablename__ = "market_tasks"

    object_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("objects.id"), unique=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # Район без дома и квартиры — точный адрес подрядчик увидит, когда его выберут.
    area_hint: Mapped[str] = mapped_column(String(100), nullable=False)
    work_types: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    budget_kop: Mapped[int | None] = mapped_column(BigInteger)
    desired_start: Mapped[date | None] = mapped_column(Date)
    bids_until: Mapped[date | None] = mapped_column(Date)
    # open → awarded | closed
    status: Mapped[str] = mapped_column(
        String(10), nullable=False, default="open", server_default="open"
    )
    # Выбранный отклик (без внешнего ключа: отклики ссылаются на заявку).
    awarded_bid_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    awarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MarketBid(UUIDPk, Timestamps, Base):
    """Отклик подрядчика: цена работ, срок, когда может начать."""

    __tablename__ = "market_bids"
    __table_args__ = (UniqueConstraint("task_id", "org_id", name="uq_market_bid_org"),)

    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("market_tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    price_kop: Mapped[int] = mapped_column(BigInteger, nullable=False)
    days: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    start_date: Mapped[date | None] = mapped_column(Date)
    message: Mapped[str | None] = mapped_column(Text)
    # submitted → accepted | rejected | withdrawn
    status: Mapped[str] = mapped_column(
        String(10), nullable=False, default="submitted", server_default="submitted"
    )


class ContractorReview(UUIDPk, Base):
    """Отзыв заказчика о подрядчике после завершения объекта."""

    __tablename__ = "contractor_reviews"
    __table_args__ = (UniqueConstraint("object_id", "author_id", name="uq_review_object_author"),)

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    object_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("objects.id"), nullable=False)
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    rating: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
