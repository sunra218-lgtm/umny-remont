"""«Люкс ремонт»: расчёт доставки и таможни, стартовая витрина, лента заказа."""

import logging
import math
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import uuid7
from app.core.problems import Problem
from app.core.security import utcnow
from app.domain.lux import (
    ASSEMBLY_MIN_KOP,
    ASSEMBLY_RATE,
    DUTY_RATE,
    INSURANCE_RATE,
    NO_CUSTOMS,
    ORDER_STATUSES,
    ORDER_TRANSITIONS,
    REGIONS,
    STARTER_ITEMS,
    VAT_RATE,
    region,
    to_rub_kop,
)
from app.models import FurnitureEvent, FurnitureItem, FurnitureOrder, FurnitureOrderItem
from app.modules.lux import integrations as ext

log = logging.getLogger(__name__)


class Leg(BaseModel):
    region: str
    countries: list[str]
    volume_m3: float
    goods_kop: int
    delivery_kop: int
    customs_kop: int
    days: int


class LogisticsEstimate(BaseModel):
    legs: list[Leg]
    goods_kop: int
    delivery_kop: int
    customs_kop: int
    insurance_kop: int
    assembly_kop: int
    total_kop: int
    days: int  # от заказа до объекта: производство + путь
    source: str  # tariffs | partner
    carrier: str | None = None
    # Позиции «найдите мне» без цены в расчёт не входят.
    unpriced: int = 0


@dataclass
class _Group:
    countries: set[str] = field(default_factory=set)
    litres: int = 0
    goods: int = 0
    customs_base: int = 0
    lead: int = 0


def _price(line: FurnitureOrderItem) -> int | None:
    return line.price_kop if line.price_kop is not None else line.estimate_kop


async def estimate(
    db: AsyncSession, order: FurnitureOrder, *, use_partner: bool = True
) -> LogisticsEstimate:
    lines = list(
        (
            await db.execute(
                select(FurnitureOrderItem).where(FurnitureOrderItem.order_id == order.id)
            )
        ).scalars()
    )
    item_ids = [x.item_id for x in lines if x.item_id]
    items = (
        {
            i.id: i
            for i in (
                await db.execute(select(FurnitureItem).where(FurnitureItem.id.in_(item_ids)))
            ).scalars()
        }
        if item_ids
        else {}
    )
    groups: dict[str, _Group] = defaultdict(_Group)
    cargo = []
    unpriced = 0
    goods_total = 0
    for line in lines:
        price = _price(line)
        if price is None:
            unpriced += 1
            continue
        it = items.get(line.item_id) if line.item_id else None
        country = it.country if it else "IT"
        litres = (it.volume_l if it else 500) * line.qty
        weight = (it.weight_kg if it else 50) * line.qty
        goods = price * line.qty
        goods_total += goods
        g = groups[region(country)]
        g.countries.add(country)
        g.litres += litres
        g.goods += goods
        if country not in NO_CUSTOMS:
            g.customs_base += goods
        g.lead = max(g.lead, it.lead_time_days if it else 60)
        cargo.append(
            {"origin_country": country, "volume_l": litres, "weight_kg": weight, "goods_kop": goods}
        )

    legs: list[Leg] = []
    for reg, g in groups.items():
        per_m3, minimum, days = REGIONS[reg]
        m3 = g.litres / 1000
        delivery = max(minimum, math.ceil(per_m3 * m3)) * 100
        customs = 0
        if g.customs_base:
            taxable = g.customs_base + delivery * g.customs_base / g.goods
            duty = taxable * DUTY_RATE
            customs = round(duty + (taxable + duty) * VAT_RATE)
        legs.append(
            Leg(
                region=reg,
                countries=sorted(g.countries),
                volume_m3=round(m3, 2),
                goods_kop=g.goods,
                delivery_kop=delivery,
                customs_kop=customs,
                days=days + g.lead,
            )
        )
    delivery = sum(x.delivery_kop for x in legs)
    customs = sum(x.customs_kop for x in legs)
    days = max((x.days for x in legs), default=0)
    source, carrier = "tariffs", None
    if use_partner and cargo and ext.logistics_enabled():
        try:
            q = await ext.logistics_quote(cargo)
            delivery = q.delivery_kop
            if q.customs_kop is not None:
                customs = q.customs_kop
            lead = max((x.days - REGIONS[x.region][2] for x in legs), default=0)
            days, source, carrier = lead + q.days, "partner", q.carrier
        except Exception as exc:  # noqa: BLE001 — партнёр недоступен: считаем по тарифам
            log.warning("Расчёт логистики у партнёра не удался: %s", exc)
    insurance = round(goods_total * INSURANCE_RATE)
    assembly = max(ASSEMBLY_MIN_KOP, round(goods_total * ASSEMBLY_RATE)) if goods_total else 0
    return LogisticsEstimate(
        legs=legs,
        goods_kop=goods_total,
        delivery_kop=delivery,
        customs_kop=customs,
        insurance_kop=insurance,
        assembly_kop=assembly,
        total_kop=goods_total + delivery + customs + insurance + assembly,
        days=days,
        source=source,
        carrier=carrier,
        unpriced=unpriced,
    )


def event(
    db: AsyncSession, order: FurnitureOrder, kind: str, text: str, author: uuid.UUID | None
) -> None:
    db.add(
        FurnitureEvent(id=uuid7(), order_id=order.id, kind=kind, text=text[:1000], author_id=author)
    )


def move(order: FurnitureOrder, to: str, *, by_customer: bool, by_manager: bool) -> None:
    allowed = ORDER_TRANSITIONS.get(order.status, {}).get(to)
    if allowed is None:
        raise Problem(
            409,
            "bad-transition",
            "Так сменить статус нельзя",
            f"Сейчас: {ORDER_STATUSES[order.status]}.",
        )
    if not ((by_customer and "c" in allowed) or (by_manager and "m" in allowed)):
        raise Problem(403, "forbidden", "Этот шаг делает другая сторона")
    order.status = to
    now = utcnow()
    if to == "requested":
        order.submitted_at = now
    elif to == "quoted":
        order.quoted_at = now
    elif to == "confirmed":
        order.confirmed_at = now


async def next_number(db: AsyncSession) -> int:
    return int(await db.scalar(select(func.coalesce(func.max(FurnitureOrder.number), 0))) or 0) + 1


def quote_expired(order: FurnitureOrder) -> bool:
    return (
        order.status == "quoted"
        and order.quote_valid_until is not None
        and order.quote_valid_until < (utcnow() + timedelta(hours=3)).date()
    )


async def seed_items(db: AsyncSession) -> int:
    if await db.scalar(select(FurnitureItem.id).limit(1)):
        return 0
    for title, cat, country, styles, about, dims, price, cur, litres, kg, days in STARTER_ITEMS:
        db.add(
            FurnitureItem(
                id=uuid.uuid4(),
                source="catalog",
                title=title,
                category=cat,
                country=country,
                styles=styles,
                description=about,
                dimensions=dims,
                price=price,
                currency=cur,
                price_rub_kop=to_rub_kop(price, cur),
                volume_l=litres,
                weight_kg=kg,
                lead_time_days=days,
            )
        )
    await db.commit()
    return len(STARTER_ITEMS)
