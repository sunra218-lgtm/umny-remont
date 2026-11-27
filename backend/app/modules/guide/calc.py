"""Калькулятор расхода материалов по техкарте."""

import math
from typing import Any

from pydantic import BaseModel


class CalcRow(BaseModel):
    key: str
    name: str
    unit: str
    per: str
    quantity: float
    thickness_mm: float | None
    packs: int | None
    pack_title: str | None
    note: str | None


def calculate(
    materials: list[dict[str, Any]],
    amount: dict[str, float],
    thickness: dict[str, float] | None = None,
) -> list[CalcRow]:
    """Расход = норма × объём работ (× толщина, если норма на 1 мм) + запас.

    ``amount`` — объём работ по единицам («м²», «м.п.», «шт», «м³»); материал, для единицы
    которого объём не задан, не считается. ``thickness`` — толщина слоя по коду материала.
    """
    rows = []
    for m in materials:
        qty = amount.get(m["per"])
        if not qty or qty <= 0:
            continue
        th = None
        value = m["rate"] * qty
        if m.get("per_mm"):
            th = (thickness or {}).get(m["key"]) or m.get("thickness_mm") or 1
            value *= th
        value *= 1 + m.get("reserve_pct", 0) / 100
        packs = math.ceil(round(value / m["pack"], 6)) if m.get("pack") else None
        rows.append(
            CalcRow(
                key=m["key"],
                name=m["name"],
                unit=m["unit"],
                per=m["per"],
                quantity=round(value, 1 if value < 100 else 0),
                thickness_mm=th,
                packs=packs,
                pack_title=m.get("pack_title"),
                note=m.get("note"),
            )
        )
    return rows
