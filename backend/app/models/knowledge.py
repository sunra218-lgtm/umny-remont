"""Технологические карты и дневные планы работ."""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    false,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamps, UUIDPk


class TechCard(UUIDPk, Timestamps, Base):
    """Технологическая карта этапа: шаги, допуски по нормам, материалы с расходом.

    Шаги и материалы — JSON (формат описан в app/domain/tech_cards.py): карту правят целиком,
    а пункты чек-листа ракурсов собираются из шагов автоматически.
    """

    __tablename__ = "tech_cards"

    stage_type: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    norms: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    steps: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    materials: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    # Проверено инженером технадзора; после правки карты отметка снимается.
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class DailyPlan(UUIDPk, Timestamps, Base):
    """План работ бригады на день и его приёмка заказчиком или технадзором."""

    __tablename__ = "daily_plans"
    __table_args__ = (UniqueConstraint("object_id", "day", name="uq_daily_plan_day"),)

    object_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("objects.id"), nullable=False, index=True
    )
    day: Mapped[date] = mapped_column(Date, nullable=False)
    # planned → reported → accepted | returned → reported …
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="planned", server_default="planned"
    )
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    report_note: Mapped[str | None] = mapped_column(Text)
    reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)


class DailyPlanItem(UUIDPk, Base):
    __tablename__ = "daily_plan_items"

    plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("daily_plans.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    stage_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("stages.id"))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    done: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    note: Mapped[str | None] = mapped_column(String(500))
