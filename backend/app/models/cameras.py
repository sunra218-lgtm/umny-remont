"""Камеры на объекте: таймлапс с IP-камеры или старого телефона.

Снимают только когда заказчик включил камеру (с согласием) и только в рабочие часы объекта.
Кадры лежат в том же хранилище, что и фотоотчёты (Object Storage в РФ), и удаляются
через ``camera_retention_days``.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    false,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamps, UUIDPk


class Camera(UUIDPk, Timestamps, Base):
    __tablename__ = "cameras"

    object_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("objects.id"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    # ip — сервер сам забирает снимок по ссылке; phone — старый телефон присылает кадры.
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    snapshot_url: Mapped[str | None] = mapped_column(String(500))
    # Для телефона: хеш ключа из ссылки, которую открывают на телефоне-камере.
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    interval_min: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=15, server_default="15"
    )
    hour_from: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=9, server_default="9"
    )
    hour_to: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=20, server_default="20"
    )
    # Включает и выключает только заказчик; включение — с согласием на съёмку.
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consent_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    last_frame_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(300))
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CameraFrame(UUIDPk, Base):
    __tablename__ = "camera_frames"

    camera_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cameras.id"), nullable=False, index=True
    )
    object_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("objects.id"), nullable=False, index=True
    )
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    storage_key: Mapped[str] = mapped_column(String(300), nullable=False)
    content_type: Mapped[str] = mapped_column(String(50), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    phash: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Кадр заметно отличается от предыдущего — на объекте что-то происходило.
    changed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    # Распознавание: этап, скрытые работы, метки. Автоматически или вручную инженером.
    stage_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("stages.id"))
    hidden_work: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    labels: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    labeled_by: Mapped[str] = mapped_column(
        String(20), nullable=False, default="auto", server_default="auto"
    )
    labeled_user: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
