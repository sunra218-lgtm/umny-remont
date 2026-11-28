"""«Люкс ремонт»: дизайнерская мебель со всего мира, заказ через персонального менеджера,
доставка и логистика.

Пакет объекта (``RepairObject.package``) и VIP-статус подрядчика (``Organization.vip_at``)
лежат в своих таблицах.
"""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamps, UUIDPk


class FurnitureItem(UUIDPk, Timestamps, Base):
    """Позиция каталога дизайнерской мебели: своя или от поставщика-партнёра."""

    __tablename__ = "furniture_items"

    # Откуда позиция: catalog — внесена менеджером, иначе код поставщика-партнёра.
    source: Mapped[str] = mapped_column(String(30), nullable=False, default="catalog")
    external_id: Mapped[str | None] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(150))
    designer: Mapped[str | None] = mapped_column(String(150))
    # sofa, armchair, chair, table, bed, storage, lighting, rug, decor, kitchen, bath
    category: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    country: Mapped[str] = mapped_column(String(2), nullable=False)  # ISO-код страны
    styles: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    description: Mapped[str | None] = mapped_column(Text)
    dimensions: Mapped[str | None] = mapped_column(String(100))
    # Цена у поставщика в его валюте и ориентир в рублях (точную цену фиксирует менеджер).
    price: Mapped[int] = mapped_column(BigInteger, nullable=False)  # в минимальных единицах
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")
    price_rub_kop: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Для расчёта доставки: объём в упаковке (дм³ → храним литры) и вес.
    volume_l: Mapped[int] = mapped_column(Integer, nullable=False, default=500)
    weight_kg: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    image_url: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")


class FurnitureOrder(UUIDPk, Timestamps, Base):
    """Заказ мебели по объекту. Ведёт персональный менеджер.

    draft → requested → quoted → confirmed → paid → ordered → shipping → delivered → installed;
    cancelled — из любого статуса до оплаты.
    """

    __tablename__ = "furniture_orders"

    object_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("objects.id"), nullable=False, index=True
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(15), nullable=False, default="draft")
    customer_note: Mapped[str | None] = mapped_column(Text)
    manager_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    manager_note: Mapped[str | None] = mapped_column(Text)
    # Итоговое предложение менеджера (в копейках).
    goods_kop: Mapped[int | None] = mapped_column(BigInteger)
    delivery_kop: Mapped[int | None] = mapped_column(BigInteger)
    customs_kop: Mapped[int | None] = mapped_column(BigInteger)
    service_kop: Mapped[int | None] = mapped_column(BigInteger)
    total_kop: Mapped[int | None] = mapped_column(BigInteger)
    eta: Mapped[date | None] = mapped_column(Date)
    quote_valid_until: Mapped[date | None] = mapped_column(Date)
    carrier: Mapped[str | None] = mapped_column(String(100))
    tracking: Mapped[str | None] = mapped_column(String(100))
    # Ответ внешней логистики, если она подключена.
    logistics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FurnitureOrderItem(UUIDPk, Base):
    __tablename__ = "furniture_order_items"

    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("furniture_orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Позиция каталога или запрос «найдите мне» (тогда item_id пуст, есть описание и ссылка).
    item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("furniture_items.id"))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    wish: Mapped[str | None] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(String(500))
    room: Mapped[str | None] = mapped_column(String(60))
    qty: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    estimate_kop: Mapped[int | None] = mapped_column(BigInteger)  # ориентир из каталога
    price_kop: Mapped[int | None] = mapped_column(BigInteger)  # цена за штуку от менеджера


class FurnitureEvent(UUIDPk, Base):
    """Лента заказа: статусы, отметки логистики, сообщения менеджера."""

    __tablename__ = "furniture_events"

    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("furniture_orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # status | logistics | note
    text: Mapped[str] = mapped_column(String(1000), nullable=False)
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
