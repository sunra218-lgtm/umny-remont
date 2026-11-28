"""«Люкс ремонт»: пакет, VIP-подрядчики, менеджер, каталог мебели, заказ, доставка."""

from datetime import date, timedelta

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import get_session
from app.models import FurnitureOrder, Notification
from app.modules.lux import integrations as ext
from app.modules.lux import jobs, service
from tests.conftest import idem, login
from tests.scenario import build_world, post

MANAGER = "+79160000021"


async def _seed() -> int:
    async for db in get_session():
        return await service.seed_items(db)
    return 0


async def _put(client: AsyncClient, url: str, h: dict[str, str], body):  # type: ignore[no-untyped-def]
    return await client.put(url, json=body, headers={**h, **idem()})


async def _templates(engine) -> set[str]:  # type: ignore[no-untyped-def]
    async with engine.begin() as conn:
        return set((await conn.execute(select(Notification.template))).scalars().all())


async def test_lux_flow(client: AsyncClient, admin: dict[str, str], engine) -> None:  # type: ignore[no-untyped-def]
    assert await _seed() > 15
    w = await build_world(client, admin)
    obj = f"/api/v1/objects/{w.object_id}"
    r = await post(
        client,
        f"/api/v1/organizations/{w.platform_org_id}/members",
        admin,
        {"phone": MANAGER, "role": "procurement_manager"},
    )
    manager_id = r.json()["user_id"]
    manager = await login(client, MANAGER)

    # Каталог смотрит любой, фильтры работают.
    items = (await client.get("/api/v1/furniture?category=sofa", headers=w.customer)).json()
    assert items and {i["category"] for i in items} == {"sofa"}
    italy = (await client.get("/api/v1/furniture?country=IT", headers=w.customer)).json()
    assert italy and all(i["country"] == "IT" for i in italy)
    sofa = next(i for i in items if i["country"] == "IT")
    assert sofa["price_rub_kop"] > 0

    # Без пакета «Люкс» мебель не заказать.
    line = {"item_id": sofa["id"], "room": "Гостиная"}
    r = await post(client, f"{obj}/furniture-orders/lines", w.customer, line)
    assert r.status_code == 409

    # Подрядчик объекта не VIP — сначала VIP-статус.
    r = await _put(client, f"{obj}/package", w.customer, {"package": "lux"})
    assert r.status_code == 409 and r.json()["type"] == "/problems/contractor-not-vip"
    assert (
        await _put(client, f"/api/v1/contractors/{w.org_id}/vip", w.boss, {"vip": True})
    ).status_code == 403
    assert (
        await _put(client, f"/api/v1/contractors/{w.org_id}/vip", admin, {"vip": True})
    ).json() == {"vip": True}
    lux = (await _put(client, f"{obj}/package", w.customer, {"package": "lux"})).json()
    assert lux["package"] == "lux" and lux["contractor_vip"] and lux["manager"] is None
    assert "lux_requested" in await _templates(engine)
    o = (await client.get(obj, headers=w.customer)).json()
    assert o["package"] == "lux"

    # Менеджер — только сотрудник платформы.
    bad = await _put(client, f"{obj}/lux-manager", admin, {"user_id": w.user_ids["foreman"]})
    assert bad.status_code == 422
    assert (
        await _put(client, f"{obj}/lux-manager", w.customer, {"user_id": manager_id})
    ).status_code == 403
    lux = (await _put(client, f"{obj}/lux-manager", admin, {"user_id": manager_id})).json()
    assert lux["manager"]["id"] == manager_id
    seen = (await client.get(f"{obj}/lux", headers=w.foreman)).json()
    assert seen["manager"]["phone"] == MANAGER and not seen["can_change"]

    # Подборка: из каталога и «найдите мне» по ссылке.
    r = await post(client, f"{obj}/furniture-orders/lines", w.customer, {**line, "qty": 1})
    assert r.status_code == 201, r.text
    wish = {
        "wish": "Кресло как на фото, зелёный бархат",
        "link": "https://example.com/chair",
        "qty": 2,
    }
    order = (await post(client, f"{obj}/furniture-orders/lines", w.customer, wish)).json()
    assert len(order["lines"]) == 2 and order["status"] == "draft" and order["can_edit"]
    assert (
        await post(client, f"{obj}/furniture-orders/lines", w.customer, {"qty": 1})
    ).status_code == 422
    assert (await post(client, f"{obj}/furniture-orders/lines", w.foreman, line)).status_code == 403
    sofa_line = next(x for x in order["lines"] if x["item_id"])
    order = (
        await _put(
            client, f"/api/v1/furniture-order-lines/{sofa_line['id']}", w.customer, {"qty": 2}
        )
    ).json()
    assert next(x for x in order["lines"] if x["item_id"])["qty"] == 2

    est = (
        await client.get(f"/api/v1/furniture-orders/{order['id']}/estimate", headers=w.customer)
    ).json()
    assert est["unpriced"] == 1 and est["goods_kop"] == sofa["price_rub_kop"] * 2
    assert est["delivery_kop"] > 0 and est["customs_kop"] > 0 and est["source"] == "tariffs"
    assert est["days"] > 30 and est["legs"][0]["region"] == "europe"

    oid = order["id"]
    st = f"/api/v1/furniture-orders/{oid}/status"
    order = (await post(client, st, w.customer, {"status": "requested"})).json()
    assert order["status"] == "requested" and not order["can_edit"]
    assert "furniture_requested" in await _templates(engine)
    q = (await client.get("/api/v1/lux/orders?mine=true", headers=manager)).json()
    assert [x["id"] for x in q] == [oid]
    assert (await client.get("/api/v1/lux/orders", headers=w.customer)).status_code == 403
    assert (await post(client, st, w.customer, {"status": "paid"})).status_code == 409

    # Предложение менеджера с ценой каждой позиции.
    eta = (date.today() + timedelta(days=75)).isoformat()
    valid = (date.today() + timedelta(days=7)).isoformat()
    quote = {
        "lines": [{"id": order["lines"][0]["id"], "price_kop": 120_000_000}],
        "delivery_kop": est["delivery_kop"],
        "customs_kop": est["customs_kop"],
        "service_kop": 0,
        "eta": eta,
        "valid_until": valid,
    }
    assert (
        await _put(client, f"/api/v1/furniture-orders/{oid}/quote", w.customer, quote)
    ).status_code == 403
    assert (
        await _put(client, f"/api/v1/furniture-orders/{oid}/quote", manager, quote)
    ).status_code == 422
    quote["lines"] = [
        {"id": x["id"], "price_kop": 120_000_000 if x["item_id"] else 35_000_000, "title": None}
        for x in order["lines"]
    ]
    order = (await _put(client, f"/api/v1/furniture-orders/{oid}/quote", manager, quote)).json()
    assert order["status"] == "quoted"
    assert order["goods_kop"] == 120_000_000 * 2 + 35_000_000 * 2
    assert order["total_kop"] == order["goods_kop"] + est["delivery_kop"] + est["customs_kop"]
    assert "furniture_quoted" in await _templates(engine)

    # Заказчик подтверждает, дальше ведёт менеджер.
    order = (await post(client, st, w.customer, {"status": "confirmed"})).json()
    assert order["status"] == "confirmed"
    assert (await post(client, st, w.customer, {"status": "paid"})).status_code == 403
    for s in ("paid", "ordered"):
        order = (await post(client, st, manager, {"status": s})).json()
        assert order["status"] == s
    lg = f"/api/v1/furniture-orders/{oid}/logistics"
    order = (
        await post(
            client,
            lg,
            manager,
            {
                "carrier": "Карго Европа",
                "tracking": "EU123",
                "text": "Отгружено с фабрики в Милане",
            },
        )
    ).json()
    assert order["tracking"] == "EU123"
    assert (await post(client, lg, w.customer, {"text": "Где?"})).status_code == 403
    for s in ("shipping", "delivered", "installed"):
        order = (await post(client, st, manager, {"status": s})).json()
    assert order["status"] == "installed"
    texts = [e["text"] for e in order["events"]]
    assert "Отгружено с фабрики в Милане" in texts and texts[-1].startswith("Собран")
    assert (await client.get(f"{obj}/furniture-orders", headers=w.foreman)).status_code == 200
    assert (await client.get(f"{obj}/furniture-orders", headers=w.stranger)).status_code == 404

    # Каталог ведёт менеджер.
    body = {
        "title": "Стул из ротанга",
        "category": "chair",
        "country": "ID",
        "price": 45_000,
        "currency": "USD",
        "styles": ["japandi"],
    }
    assert (await post(client, "/api/v1/furniture", w.customer, body)).status_code == 403
    item = (await post(client, "/api/v1/furniture", manager, body)).json()
    assert item["price_rub_kop"] == 450 * 88 * 100
    off = (
        await _put(client, f"/api/v1/furniture/{item['id']}", manager, {**body, "is_active": False})
    ).json()
    assert not off["is_active"]
    assert (
        await post(client, "/api/v1/furniture", manager, {**body, "country": "XX"})
    ).status_code == 422


async def test_vip_only_for_lux(client: AsyncClient, admin: dict[str, str]) -> None:
    w = await build_world(client, admin, contractor=False)
    obj = f"/api/v1/objects/{w.object_id}"
    assert (await _put(client, f"{obj}/package", w.customer, {"package": "lux"})).status_code == 200
    r = await _put(client, f"{obj}/contractor", admin, {"contractor_org_id": w.org_id})
    assert r.status_code == 409 and r.json()["type"] == "/problems/contractor-not-vip"

    # Заявку на маркетплейсе «Люкс» не-VIP подрядчик не видит и не может откликнуться.
    await _put(client, f"{obj}/match-mode", w.customer, {"mode": "market"})
    t = (
        await _put(
            client,
            f"{obj}/market-task",
            w.customer,
            {"title": "Ремонт", "area_hint": "Москва, ЦАО"},
        )
    ).json()
    assert t["lux"]
    assert (await client.get("/api/v1/market/tasks", headers=w.boss)).json() == []
    bid = {"price_kop": 100_000_000, "days": 60}
    assert (
        await post(client, f"/api/v1/market-tasks/{t['id']}/bids", w.boss, bid)
    ).status_code == 403
    await _put(client, f"/api/v1/contractors/{w.org_id}/vip", admin, {"vip": True})
    assert len((await client.get("/api/v1/market/tasks", headers=w.boss)).json()) == 1
    assert (
        await post(client, f"/api/v1/market-tasks/{t['id']}/bids", w.boss, bid)
    ).status_code == 201
    cards = (await client.get("/api/v1/contractors?vip=true", headers=w.customer)).json()
    assert [c["org_id"] for c in cards] == [w.org_id] and cards[0]["vip"]


async def test_estimate_without_customs_inside_eaeu(
    client: AsyncClient, admin: dict[str, str]
) -> None:
    await _seed()
    w = await build_world(client, admin, contractor=False)
    obj = f"/api/v1/objects/{w.object_id}"
    await _put(client, f"{obj}/package", w.customer, {"package": "lux"})
    ru = (await client.get("/api/v1/furniture?country=RU", headers=w.customer)).json()[0]
    order = (
        await post(client, f"{obj}/furniture-orders/lines", w.customer, {"item_id": ru["id"]})
    ).json()
    est = (
        await client.get(f"/api/v1/furniture-orders/{order['id']}/estimate", headers=w.customer)
    ).json()
    assert est["customs_kop"] == 0 and est["delivery_kop"] == 3_000 * 100
    assert est["assembly_kop"] == max(5_000_00, round(ru["price_rub_kop"] * 0.05))


async def test_partner_integrations(  # type: ignore[no-untyped-def]
    client: AsyncClient, admin: dict[str, str], monkeypatch: pytest.MonkeyPatch, engine
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer k"
        if request.url.path == "/search":
            good = {
                "id": "A1",
                "supplier": "milano",
                "title": "Диван",
                "category": "sofa",
                "country": "IT",
                "price": 500_000,
                "currency": "EUR",
                "image_url": "http://insecure/img.jpg",
            }
            return httpx.Response(200, json={"items": [good, {"id": "bad"}]})
        if request.url.path == "/quotes":
            body = request.read().decode()
            assert "phone" not in body and "address" not in body
            return httpx.Response(
                200,
                json={
                    "delivery_kop": 7_000_000,
                    "customs_kop": 1_000_000,
                    "days": 20,
                    "carrier": "X",
                },
            )
        return httpx.Response(
            200, json={"events": [{"at": "2026-12-01", "text": "Прибыл в Москву"}]}
        )

    t = httpx.MockTransport(handler)
    s = get_settings()
    monkeypatch.setattr(s, "furniture_search_url", "https://partner.example")
    monkeypatch.setattr(s, "furniture_search_key", "k")
    monkeypatch.setattr(s, "logistics_url", "https://cargo.example")
    monkeypatch.setattr(s, "logistics_key", "k")
    found = await ext.search_partners("диван", None, transport=t)
    assert len(found) == 1 and found[0].image_url is None
    q = await ext.logistics_quote([{"origin_country": "IT"}], transport=t)
    assert q.delivery_kop == 7_000_000 and q.days == 20
    assert await ext.tracking("EU1", transport=t) == [
        {"at": "2026-12-01", "text": "Прибыл в Москву"}
    ]

    # Через API: поиск у партнёров сохраняет позиции в каталог.
    async def fake_search(q: str, category: str | None):  # type: ignore[no-untyped-def]
        return found

    async def fake_quote(cargo):  # type: ignore[no-untyped-def]
        return q

    async def fake_tracking(number: str):  # type: ignore[no-untyped-def]
        return [{"at": "2026-12-01", "text": "Прибыл в Москву"}]

    monkeypatch.setattr(ext, "search_partners", fake_search)
    monkeypatch.setattr(ext, "logistics_quote", fake_quote)
    monkeypatch.setattr(ext, "tracking", fake_tracking)
    w = await build_world(client, admin, contractor=False)
    items = (await client.get("/api/v1/furniture/partners?q=диван", headers=w.customer)).json()
    assert items[0]["source"] == "milano" and items[0]["price_rub_kop"] == 5000 * 95 * 100
    again = (await client.get("/api/v1/furniture/partners?q=диван", headers=w.customer)).json()
    assert again[0]["id"] == items[0]["id"]

    obj = f"/api/v1/objects/{w.object_id}"
    await _put(client, f"{obj}/package", w.customer, {"package": "lux"})
    order = (
        await post(client, f"{obj}/furniture-orders/lines", w.customer, {"item_id": items[0]["id"]})
    ).json()
    est = (
        await client.get(f"/api/v1/furniture-orders/{order['id']}/estimate", headers=w.customer)
    ).json()
    assert est["source"] == "partner" and est["delivery_kop"] == 7_000_000 and est["carrier"] == "X"

    async with engine.begin() as conn:
        await conn.execute(
            FurnitureOrder.__table__.update()
            .where(FurnitureOrder.id == order["id"])
            .values(status="shipping", tracking="EU1")
        )
    async for db in get_session():
        assert await jobs.poll_tracking(db) == 1
        assert await jobs.poll_tracking(db) == 0
    view = (await client.get(f"{obj}/furniture-orders", headers=w.customer)).json()
    assert view[0]["events"][-1]["text"] == "Прибыл в Москву"
