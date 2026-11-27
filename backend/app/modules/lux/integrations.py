"""Внешние сервисы «Люкс ремонта». Каждый включается адресом в настройках, по умолчанию выключен.

* Поиск у поставщиков-партнёров: ``FURNITURE_SEARCH_URL``. Без него ищем по своему каталогу.
* Логистика: ``LOGISTICS_URL``. Без неё доставку и таможню считаем по тарифам платформы,
  а отметки о пути вносит менеджер.

Наружу уходят только поисковый запрос, страна отправки, объём, вес и стоимость груза — без имени,
телефона и адреса заказчика. Формат обмена — docs/lux.md.
"""

from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.core.config import get_settings


class IntegrationError(Exception):
    pass


def partners_enabled() -> bool:
    return bool(get_settings().furniture_search_url)


def logistics_enabled() -> bool:
    return bool(get_settings().logistics_url)


def _headers(key: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"} if key else {}


class PartnerItem(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    supplier: str = Field(min_length=1, max_length=30)
    title: str = Field(min_length=1, max_length=300)
    brand: str | None = Field(default=None, max_length=150)
    designer: str | None = Field(default=None, max_length=150)
    category: str
    country: str = Field(min_length=2, max_length=2)
    description: str | None = Field(default=None, max_length=5000)
    dimensions: str | None = Field(default=None, max_length=100)
    price: int = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    volume_l: int = Field(default=500, gt=0, le=100_000)
    weight_kg: int = Field(default=50, gt=0, le=10_000)
    lead_time_days: int = Field(default=60, ge=1, le=730)
    image_url: str | None = Field(default=None, max_length=500)


async def search_partners(
    q: str, category: str | None, transport: httpx.AsyncBaseTransport | None = None
) -> list[PartnerItem]:
    s = get_settings()
    if not s.furniture_search_url:
        raise IntegrationError("Поиск у партнёров не подключён")
    params = {"q": q}
    if category:
        params["category"] = category
    async with httpx.AsyncClient(timeout=15, transport=transport) as client:
        resp = await client.get(
            f"{s.furniture_search_url.rstrip('/')}/search",
            params=params,
            headers=_headers(s.furniture_search_key),
        )
        resp.raise_for_status()
        raw = resp.json().get("items", [])
    out = []
    for r in raw[:50]:
        try:
            item = PartnerItem.model_validate(r)
        except ValidationError:
            continue
        if item.image_url and not item.image_url.startswith("https://"):
            item.image_url = None
        out.append(item)
    return out


class LogisticsQuote(BaseModel):
    delivery_kop: int = Field(ge=0)
    customs_kop: int | None = Field(default=None, ge=0)
    days: int = Field(ge=1, le=365)
    carrier: str | None = Field(default=None, max_length=100)


async def logistics_quote(
    cargo: list[dict[str, Any]], transport: httpx.AsyncBaseTransport | None = None
) -> LogisticsQuote:
    """cargo: [{origin_country, volume_l, weight_kg, goods_kop}] — без данных заказчика."""
    s = get_settings()
    if not s.logistics_url:
        raise IntegrationError("Логистика не подключена")
    async with httpx.AsyncClient(timeout=20, transport=transport) as client:
        resp = await client.post(
            f"{s.logistics_url.rstrip('/')}/quotes",
            json={"destination_country": "RU", "cargo": cargo},
            headers=_headers(s.logistics_key),
        )
        resp.raise_for_status()
        return LogisticsQuote.model_validate(resp.json())


async def tracking(
    number: str, transport: httpx.AsyncBaseTransport | None = None
) -> list[dict[str, str]]:
    """События пути: [{at, text}] в порядке времени."""
    s = get_settings()
    if not s.logistics_url:
        raise IntegrationError("Логистика не подключена")
    async with httpx.AsyncClient(timeout=20, transport=transport) as client:
        resp = await client.get(
            f"{s.logistics_url.rstrip('/')}/tracking/{number}", headers=_headers(s.logistics_key)
        )
        resp.raise_for_status()
        events = resp.json().get("events", [])
    return [
        {"at": str(e.get("at", ""))[:40], "text": str(e.get("text", ""))[:500]}
        for e in events
        if e.get("text")
    ][:200]
