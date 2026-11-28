"""«Ремонт с душой» и куратор проекта.

* Ремонт с душой (``RepairObject.soul``): финальное слово в дизайне, оформлении и наполнении
  предметами — за живым дизайнером интерьера, а не за алгоритмом.
* Куратор (``RepairObject.curator_kind``): следит за ходом ремонта. ИИ-куратор — проверки по
  данным платформы; куратор-человек вдобавок видит сигналы, пишет заметки заказчику.
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamps, UUIDPk


class DesignerProfile(UUIDPk, Timestamps, Base):
    """Дизайнер интерьера на платформе: витрина и условия."""

    __tablename__ = "designer_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    about: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(String(100))
    styles: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    # Ориентир цены за м² — по нему считается предложение, итог дизайнер назначает сам.
    price_m2_kop: Mapped[int | None] = mapped_column(BigInteger)
    since_year: Mapped[int | None] = mapped_column(Integer)
    accepts_projects: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DesignerPhoto(UUIDPk, Base):
    __tablename__ = "designer_photos"

    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("designer_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    storage_key: Mapped[str] = mapped_column(String(300), nullable=False)
    content_type: Mapped[str] = mapped_column(String(50), nullable=False)
    caption: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DesignerContract(UUIDPk, Timestamps, Base):
    """Работа дизайнера над проектом объекта.

    invited → offered (дизайнер назвал цену и срок) → working (заказчик согласился)
    → submitted (дизайнер сдал) → approved (заказчик принял, дизайн финальный);
    submitted → working (заказчик попросил доработать); declined / cancelled — конец.
    """

    __tablename__ = "designer_contracts"

    object_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("objects.id"), nullable=False, index=True
    )
    designer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="invited")
    message: Mapped[str | None] = mapped_column(Text)
    fee_kop: Mapped[int | None] = mapped_column(BigInteger)
    days: Mapped[int | None] = mapped_column(Integer)
    # Вклад дизайнера: концепция, заметки по комнатам, отзыв заказчика на доработку.
    concept: Mapped[str | None] = mapped_column(Text)
    room_notes: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    feedback: Mapped[str | None] = mapped_column(Text)
    rounds: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DesignDecor(UUIDPk, Timestamps, Base):
    """Наполнение комнаты предметами: мебель, свет, текстиль, декор, искусство."""

    __tablename__ = "design_decor"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("design_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    room_key: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(String(500))
    furniture_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("furniture_items.id"))
    price_kop: Mapped[int | None] = mapped_column(BigInteger)
    qty: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    added_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)


class CuratorSignal(UUIDPk, Base):
    """Сигнал куратора: что-то идёт не так. ИИ-сигналы закрываются сами, когда всё исправили."""

    __tablename__ = "curator_signals"

    object_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("objects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # no_photos, stage_overdue, acceptance_waiting, inspection_remarks, dispute_open,
    # designer_silent, design_review, note (заметка куратора-человека)
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    ref: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    severity: Mapped[str] = mapped_column(String(10), nullable=False, default="info")
    text: Mapped[str] = mapped_column(String(1000), nullable=False)
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
