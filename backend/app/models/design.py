"""Умный дизайн-проект квартиры: стиль, план БТИ, комнаты, подобранная отделка, картинки."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamps, UUIDPk


class DesignMaterial(UUIDPk, Timestamps, Base):
    """Отделочный материал из каталога платформы (его же закупает консьерж)."""

    __tablename__ = "design_materials"

    sku: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    # laminate, parquet, porcelain, wall_tile, paint, stretch, door, plinth, light, sanitary
    kind: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(100))
    styles: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    color: Mapped[str] = mapped_column(String(7), nullable=False, default="#CCCCCC")
    # Рисунок для картинки: plank, herringbone, tile60, tile120, tile30, brick, paint, door…
    pattern: Mapped[str] = mapped_column(String(20), nullable=False, default="none")
    unit: Mapped[str] = mapped_column(String(10), nullable=False)
    price_kop: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Расход единиц на базу (м² пола, м² стен …) и размер упаковки в единицах.
    rate: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    pack_size: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    pack_title: Mapped[str | None] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )


class DesignProject(UUIDPk, Timestamps, Base):
    __tablename__ = "design_projects"

    object_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("objects.id"), unique=True, nullable=False
    )
    style: Mapped[str] = mapped_column(String(20), nullable=False)
    plan_key: Mapped[str | None] = mapped_column(String(300))
    plan_type: Mapped[str | None] = mapped_column(String(50))
    # Что удалось прочитать с плана (экспликация помещений).
    plan_text: Mapped[str | None] = mapped_column(Text)
    # [{key, name, type, area, height, perimeter, doors, windows}]
    rooms: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    # {room_key: {slot: material_id}}
    selections: Mapped[dict[str, dict[str, str]]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    # Слоты, которые заказчик поменял сам: при смене стиля их не трогаем.
    pinned: Mapped[dict[str, list[str]]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    # Ролик от внешнего контент-завода (когда подключат).
    video_status: Mapped[str | None] = mapped_column(String(20))
    video_url: Mapped[str | None] = mapped_column(String(500))
    video_job_id: Mapped[str | None] = mapped_column(String(100))
    video_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Дизайн утверждён (в «Ремонте с душой» — после сдачи дизайнером и приёмки заказчиком).
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finalized_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class DesignRender(UUIDPk, Timestamps, Base):
    """Картинка комнаты от нейросети (YandexART), если она включена."""

    __tablename__ = "design_renders"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("design_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    room_key: Mapped[str] = mapped_column(String(20), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    # queued → running → done | failed
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="queued")
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    operation_id: Mapped[str | None] = mapped_column(String(100))
    storage_key: Mapped[str | None] = mapped_column(String(300))
    error: Mapped[str | None] = mapped_column(String(300))
