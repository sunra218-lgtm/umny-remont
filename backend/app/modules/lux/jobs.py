"""Фоновая работа «Люкс ремонта»: отметки пути от логистического партнёра."""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FurnitureOrder
from app.modules.lux import integrations as ext
from app.modules.lux import service

log = logging.getLogger(__name__)


async def poll_tracking(db: AsyncSession) -> int:
    """Добавляет в ленту заказов новые события пути. Возвращает число новых событий."""
    if not ext.logistics_enabled():
        return 0
    added = 0
    orders = (
        await db.execute(
            select(FurnitureOrder)
            .where(
                FurnitureOrder.status.in_(["ordered", "shipping"]),
                FurnitureOrder.tracking.is_not(None),
            )
            .limit(50)
        )
    ).scalars()
    for o in list(orders):
        try:
            events = await ext.tracking(o.tracking or "")
        except Exception as exc:  # noqa: BLE001 — партнёр недоступен: попробуем позже
            log.warning("Трекинг заказа %s: %s", o.number, exc)
            continue
        state = dict(o.logistics or {})
        seen = set(state.get("seen", []))
        for e in events:
            key = f"{e['at']}|{e['text']}"
            if key in seen:
                continue
            seen.add(key)
            service.event(db, o, "logistics", e["text"], None)
            added += 1
        state["seen"] = sorted(seen)[-500:]
        o.logistics = state
        await db.commit()
    return added
