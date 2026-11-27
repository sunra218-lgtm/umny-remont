"""Расчёты умного дизайна: комнаты по плану, подбор отделки под стиль, смета-оценка."""

import math
import re
import uuid
from typing import Any

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.design import (
    ESTIMATE_SPREAD,
    KIND_RULES,
    KIND_WORKS,
    ROOM_SLOTS,
    ROOM_TYPES,
    ROUGH_MATERIALS,
    ROUGH_WORKS,
    SLOTS,
    STARTER_MATERIALS,
    WET,
)
from app.models import DesignMaterial, DesignProject, PriceItem

DEFAULT_HEIGHT = 2.7
DOOR_AREA = 1.7
WINDOW_AREA = 2.0
DOOR_WIDTH = 0.9


class Room(BaseModel):
    key: str = Field(min_length=1, max_length=20, pattern=r"^[a-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=60)
    type: str
    area: float = Field(gt=0, le=500)
    height: float = Field(default=DEFAULT_HEIGHT, ge=2.0, le=6.0)
    perimeter: float | None = Field(default=None, gt=0, le=500)
    doors: int = Field(default=1, ge=0, le=6)
    windows: int = Field(default=1, ge=0, le=10)

    @field_validator("type")
    @classmethod
    def _type(cls, v: str) -> str:
        if v not in ROOM_TYPES:
            raise ValueError("Неизвестный тип комнаты")
        return v

    def perim(self) -> float:
        # Без промера считаем комнату прямоугольником 1:1,3.
        if self.perimeter:
            return self.perimeter
        side = math.sqrt(self.area / 1.3)
        return round(2 * (side + side * 1.3), 2)

    def wall_area(self) -> float:
        gross = self.perim() * self.height
        return round(max(0.0, gross - self.doors * DOOR_AREA - self.windows * WINDOW_AREA), 2)


# ---------- комнаты ----------


def suggest_rooms(total_area: float | None, rooms_count: int = 2) -> list[Room]:
    """Заготовка комнат по площади квартиры, если план не прочитан: заказчик поправит."""
    total = total_area or 45.0
    bath = max(3.5, round(total * 0.07, 1))
    kitchen = max(7.0, round(total * 0.17, 1))
    hall = max(4.0, round(total * 0.12, 1))
    rest = max(10.0, total - bath - kitchen - hall)
    n = max(1, rooms_count)
    out = [
        Room(
            key="r1",
            name="Гостиная" if n > 1 else "Комната",
            type="living",
            area=round(rest / n * 1.15 if n > 1 else rest, 1),
        ),
    ]
    for i in range(1, n):
        out.append(
            Room(
                key=f"r{i + 1}",
                name="Спальня" if i == 1 else f"Комната {i + 1}",
                type="bedroom" if i == 1 else "kids",
                area=round((rest - out[0].area) / (n - 1), 1),
            )
        )
    out += [
        Room(key="kitchen", name="Кухня", type="kitchen", area=kitchen),
        Room(key="bath", name="Санузел", type="bathroom", area=bath, windows=0),
        Room(key="hall", name="Прихожая", type="hallway", area=hall, windows=0, doors=0),
    ]
    return out


# Строки экспликации БТИ: «1  Кухня  9,8» или «Жилая комната … 17.2».
ROOM_WORDS: list[tuple[str, str, str]] = [
    (r"кухн", "kitchen", "Кухня"),
    (r"сануз|с/у|ванн|туалет|уборн", "bathroom", "Санузел"),
    (r"коридор|прихож|холл", "hallway", "Прихожая"),
    (r"балкон|лоджи", "balcony", "Балкон"),
    (r"жил|комнат|спальн|гостин", "living", "Комната"),
]
AREA_RE = re.compile(r"(\d{1,3}[,.]\d{1,2})")
# Итоговые строки экспликации — не помещения.
TOTAL_RE = re.compile(r"итого|всего|общая|жилая площадь|площадь квартиры")


def parse_explication(text: str) -> list[Room]:
    """Достаёт помещения и площади из распознанного текста плана БТИ."""
    rooms: list[Room] = []
    counts: dict[str, int] = {}
    for line in text.splitlines():
        low = line.lower()
        if TOTAL_RE.search(low):
            continue
        match = next(((t, n) for pat, t, n in ROOM_WORDS if re.search(pat, low)), None)
        if match is None:
            continue
        areas = [float(a.replace(",", ".")) for a in AREA_RE.findall(line)]
        areas = [a for a in areas if 1.0 <= a <= 200]
        if not areas:
            continue
        rtype, name = match
        counts[rtype] = counts.get(rtype, 0) + 1
        k = counts[rtype]
        if rtype == "living":
            rtype = "living" if k == 1 else "bedroom"
            name = "Гостиная" if k == 1 else ("Спальня" if k == 2 else f"Комната {k}")
        elif k > 1:
            name = f"{name} {k}"
        rooms.append(
            Room(
                key=f"{rtype[:4]}{k}",
                name=name,
                type=rtype,
                area=areas[-1],
                windows=0 if rtype in ("bathroom", "hallway") else 1,
                doors=0 if rtype in ("hallway", "balcony") else 1,
            )
        )
    return rooms[:20]


# ---------- подбор отделки ----------


async def active_materials(db: AsyncSession) -> list[DesignMaterial]:
    return list(
        (
            await db.execute(
                select(DesignMaterial)
                .where(DesignMaterial.is_active.is_(True))
                .order_by(DesignMaterial.price_kop)
            )
        ).scalars()
    )


def candidates(
    materials: list[DesignMaterial], room_type: str, slot: str, style: str | None = None
) -> list[DesignMaterial]:
    kinds = ROOM_SLOTS.get(room_type, {}).get(slot, [])
    out = [m for m in materials if m.kind in kinds]
    if style:
        out = [m for m in out if style in m.styles]
    # Порядок видов задаёт приоритет (на кухне — сначала керамогранит).
    return sorted(out, key=lambda m: (kinds.index(m.kind), m.price_kop))


def pick(
    materials: list[DesignMaterial], room_type: str, slot: str, style: str, salt: int = 0
) -> DesignMaterial | None:
    """Материал под стиль; в разных комнатах стараемся не повторять одну краску."""
    styled = candidates(materials, room_type, slot, style)
    pool = styled or candidates(materials, room_type, slot)
    if not pool:
        return None
    if slot == "walls" and len(pool) > 1:
        return pool[salt % len(pool)]
    return pool[0]


def auto_select(
    project: DesignProject, rooms: list[Room], materials: list[DesignMaterial]
) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for i, room in enumerate(rooms):
        keep = set(project.pinned.get(room.key, []))
        current = project.selections.get(room.key, {})
        sel: dict[str, str] = {}
        for slot in ROOM_SLOTS[room.type]:
            if slot in keep and slot in current:
                sel[slot] = current[slot]
                continue
            m = pick(materials, room.type, slot, project.style, salt=i)
            if m is not None:
                sel[slot] = str(m.id)
        out[room.key] = sel
    return out


# ---------- оценка стоимости ----------


class Line(BaseModel):
    group: str  # finish | rough
    kind: str  # material | work
    room: str | None
    title: str
    qty: float
    unit: str
    packs: int | None = None
    pack_title: str | None = None
    unit_price_kop: int
    total_kop: int


class Estimate(BaseModel):
    lines: list[Line]
    materials_kop: int
    works_kop: int
    total_kop: int
    low_kop: int
    high_kop: int
    floor_area: float
    wall_area: float


def basis_qty(room: Room, basis: str) -> float:
    return {
        "floor": room.area,
        "ceiling": room.area,
        "walls": room.wall_area(),
        "perimeter": max(0.0, room.perim() - room.doors * DOOR_WIDTH),
        "doors": float(room.doors),
        "spots": float(max(1, math.ceil(room.area / 4))),
        "room": 1.0,
    }[basis]


async def _work_prices(db: AsyncSession) -> dict[str, int]:
    rows = await db.execute(
        select(PriceItem.title, PriceItem.unit_price_kop).where(PriceItem.is_active.is_(True))
    )
    return {t: p for t, p in rows.all()}


def _r(x: float) -> float:
    return round(x, 2)


async def estimate(db: AsyncSession, project: DesignProject) -> Estimate:
    rooms = [Room(**r) for r in project.rooms]
    mats = {str(m.id): m for m in await active_materials(db)}
    prices = await _work_prices(db)
    lines: list[Line] = []
    tile_area = 0.0
    for room in rooms:
        for slot, mid in project.selections.get(room.key, {}).items():
            m = mats.get(mid)
            if m is None:
                continue
            rule = KIND_RULES[m.kind]
            base = basis_qty(room, rule["basis"])
            if base <= 0:
                continue
            if m.kind in ("porcelain", "wall_tile"):
                tile_area += base
            qty = base * m.rate * (1 + rule["waste"])
            packs = math.ceil(qty / m.pack_size - 1e-9) if m.pack_size else None
            # Платим за целые упаковки.
            billed = packs * m.pack_size if packs else qty
            lines.append(
                Line(
                    group="finish",
                    kind="material",
                    room=room.name,
                    title=f"{SLOTS[slot]}: {m.title}",
                    qty=_r(qty),
                    unit=m.unit,
                    packs=packs,
                    pack_title=m.pack_title,
                    unit_price_kop=m.price_kop,
                    total_kop=round(billed * m.price_kop),
                )
            )
            for title, unit, default_rub in KIND_WORKS[m.kind]:
                price = prices.get(title, default_rub * 100)
                lines.append(
                    Line(
                        group="finish",
                        kind="work",
                        room=room.name,
                        title=title,
                        qty=_r(base),
                        unit=unit,
                        unit_price_kop=price,
                        total_kop=round(base * price),
                    )
                )
    floor = sum(r.area for r in rooms)
    walls = sum(r.wall_area() for r in rooms)
    bases = {
        "floor": floor,
        "walls_dry": sum(r.wall_area() for r in rooms if r.type not in WET),
        "flat": 1.0 if rooms else 0.0,
        "wet": float(sum(1 for r in rooms if r.type in WET)),
        "wet_floor": sum(r.area + 0.2 * r.perim() for r in rooms if r.type in WET),
        "tile": tile_area,
    }
    for title, unit, default_rub, basis, k in ROUGH_WORKS:
        qty = bases[basis] * k
        if qty <= 0:
            continue
        price = prices.get(title, default_rub * 100)
        lines.append(
            Line(
                group="rough",
                kind="work",
                room=None,
                title=title,
                qty=_r(qty),
                unit=unit,
                unit_price_kop=price,
                total_kop=round(qty * price),
            )
        )
    for title, unit, default_rub, basis, k in ROUGH_MATERIALS:
        qty = math.ceil(bases[basis] * k) if unit == "шт" else _r(bases[basis] * k)
        if qty <= 0:
            continue
        price = prices.get(title, default_rub * 100)
        lines.append(
            Line(
                group="rough",
                kind="material",
                room=None,
                title=title,
                qty=qty,
                unit=unit,
                unit_price_kop=price,
                total_kop=round(qty * price),
            )
        )
    mat = sum(x.total_kop for x in lines if x.kind == "material")
    work = sum(x.total_kop for x in lines if x.kind == "work")
    total = mat + work
    return Estimate(
        lines=lines,
        materials_kop=mat,
        works_kop=work,
        total_kop=total,
        low_kop=round(total * (1 - ESTIMATE_SPREAD)),
        high_kop=round(total * (1 + ESTIMATE_SPREAD)),
        floor_area=_r(floor),
        wall_area=_r(walls),
    )


# ---------- описание для картинок и роликов ----------


def room_brief(
    project: DesignProject, room: Room, mats: dict[str, DesignMaterial]
) -> dict[str, Any]:
    sel = project.selections.get(room.key, {})
    return {
        "room": room.name,
        "type": room.type,
        "area_m2": room.area,
        "finishes": {
            slot: {"title": mats[mid].title, "color": mats[mid].color, "pattern": mats[mid].pattern}
            for slot, mid in sel.items()
            if mid in mats
        },
    }


async def seed_materials(db: AsyncSession) -> int:
    if await db.scalar(select(DesignMaterial.id).limit(1)):
        return 0
    for (
        sku,
        kind,
        title,
        brand,
        styles,
        color,
        pattern,
        unit,
        rub,
        rate,
        pack,
        pack_title,
    ) in STARTER_MATERIALS:
        db.add(
            DesignMaterial(
                id=uuid.uuid4(),
                sku=sku,
                kind=kind,
                title=title,
                brand=brand,
                styles=styles,
                color=color,
                pattern=pattern,
                unit=unit,
                price_kop=rub * 100,
                rate=rate,
                pack_size=pack,
                pack_title=pack_title,
            )
        )
    await db.commit()
    return len(STARTER_MATERIALS)
