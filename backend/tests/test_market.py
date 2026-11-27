"""Маркетплейс подрядчиков: каталог, профиль, заявка, отклики, выбор, отзыв, сравнение режимов."""

from httpx import AsyncClient
from sqlalchemy import select, text

from app.models import Notification
from tests.conftest import idem, login
from tests.scenario import build_world, post
from tests.test_field import media_dir, photo  # noqa: F401

RIVAL_BOSS = "+79160000011"
NEWBIE_BOSS = "+79160000012"


async def _org(
    client: AsyncClient, admin: dict[str, str], inn: str, name: str, boss: str, verify: bool
):  # type: ignore[no-untyped-def]
    org = (
        await post(
            client,
            "/api/v1/organizations",
            admin,
            {"type": "contractor", "inn": inn, "legal_name": name},
        )
    ).json()
    if verify:
        await post(client, f"/api/v1/organizations/{org['id']}/verify", admin)
    await post(
        client,
        f"/api/v1/organizations/{org['id']}/members",
        admin,
        {"phone": boss, "role": "contractor_admin"},
    )
    return org["id"], await login(client, boss)


async def _put(client: AsyncClient, url: str, h: dict[str, str], body):  # type: ignore[no-untyped-def]
    return await client.put(url, json=body, headers={**h, **idem()})


async def test_market_flow(  # type: ignore[no-untyped-def]
    client: AsyncClient,
    admin: dict[str, str],
    engine,
    media_dir,  # noqa: F811
) -> None:
    w = await build_world(client, admin, contractor=False)
    rival_id, rival = await _org(
        client, admin, "7728168971", "ИП Бригада Петрова", RIVAL_BOSS, True
    )
    _, newbie = await _org(client, admin, "7736050003", "ООО «Новички»", NEWBIE_BOSS, False)

    # Профиль и портфолио ведёт руководитель подрядчика.
    prof = f"/api/v1/contractors/{rival_id}/profile"
    body = {
        "kind": "brigade",
        "display_name": "Бригада Петрова",
        "city": "Москва",
        "specializations": ["electric", "tiling"],
        "team_size": 4,
        "since_year": 2015,
        "about": "Электрика и плитка под ключ",
    }
    assert (await _put(client, prof, w.boss, body)).status_code == 403
    bad = await _put(client, prof, rival, {**body, "specializations": ["magic"]})
    assert bad.status_code == 422
    p = (await _put(client, prof, rival, body)).json()
    assert p["name"] == "Бригада Петрова" and p["kind"] == "brigade" and p["can_edit"]
    files = {"photo": ("p.jpg", photo(3), "image/jpeg")}
    r = await client.post(
        f"/api/v1/contractors/{rival_id}/portfolio",
        files=files,
        data={"caption": "Санузел, 2025"},
        headers={**rival, **idem()},
    )
    assert r.status_code == 201, r.text
    pic = r.json()["portfolio"][0]
    assert pic["caption"] == "Санузел, 2025"
    assert (await client.get(pic["url"])).status_code == 200

    # Каталог: только проверенные, с фильтрами.
    cat = (await client.get("/api/v1/contractors", headers=w.customer)).json()
    assert {c["name"] for c in cat} == {"ООО «Ремонт»", "Бригада Петрова"}
    brig = (await client.get("/api/v1/contractors?kind=brigade", headers=w.customer)).json()
    assert [c["name"] for c in brig] == ["Бригада Петрова"] and brig[0]["cover_url"]
    tiling = (await client.get("/api/v1/contractors?work_type=tiling", headers=w.customer)).json()
    assert [c["org_id"] for c in tiling] == [rival_id]

    # Заказчик выбирает режим и публикует заявку.
    obj = f"/api/v1/objects/{w.object_id}"
    task_body = {
        "title": "Ремонт ванной и электрика",
        "area_hint": "Москва, СВАО",
        "work_types": ["tiling"],
        "budget_kop": 50_000_000,
        "description": "Санузел 4 м², замена проводки",
    }
    assert (await _put(client, f"{obj}/market-task", w.customer, task_body)).status_code == 409
    assert (await _put(client, f"{obj}/match-mode", w.boss, {"mode": "market"})).status_code == 404
    assert (await _put(client, f"{obj}/match-mode", w.customer, {"mode": "market"})).json() == {
        "mode": "market"
    }
    t = (await _put(client, f"{obj}/market-task", w.customer, task_body)).json()
    assert t["status"] == "open" and t["bids"] == []
    async with engine.begin() as conn:
        notified = (
            (
                await conn.execute(
                    select(Notification.user_id).where(Notification.template == "market_task")
                )
            )
            .scalars()
            .all()
        )
    # Без профиля — всем проверенным; с профилем — по видам работ. Непроверенным — нет.
    assert len(notified) == 2

    # Подрядчики видят заявку без адреса и откликаются.
    assert (await client.get("/api/v1/market/tasks", headers=newbie)).status_code == 403
    seen = (await client.get("/api/v1/market/tasks", headers=rival)).json()
    assert len(seen) == 1 and "Новая" not in str(seen) and seen[0]["area_m2"] == 54.3
    bids = f"/api/v1/market-tasks/{t['id']}/bids"
    bid = {"price_kop": 45_000_000, "days": 30, "message": "Начнём через неделю"}
    assert (await post(client, bids, newbie, bid)).status_code == 403
    assert (await post(client, bids, w.foreman, bid)).status_code == 403
    mine = (await post(client, bids, rival, bid)).json()
    assert mine["my_bid"]["price_kop"] == 45_000_000 and mine["bids"] == []
    again = (await post(client, bids, rival, {**bid, "price_kop": 44_000_000})).json()
    assert again["my_bid"]["price_kop"] == 44_000_000 and again["bids_count"] == 1
    first = (await post(client, bids, w.estimator, {"price_kop": 52_000_000, "days": 25})).json()
    assert first["bids_count"] == 2
    # Пока подрядчик не выбран, адрес объекта ему недоступен.
    assert (await client.get(obj, headers=rival)).status_code == 404

    view = (await client.get(f"{obj}/market-task", headers=w.customer)).json()
    assert [b["price_kop"] for b in view["bids"]] == [44_000_000, 52_000_000]
    assert view["bids"][0]["contractor"]["name"] == "Бригада Петрова"
    rival_bid = view["bids"][0]["id"]
    assert (await post(client, f"/api/v1/market-bids/{rival_bid}/accept", rival)).status_code == 404
    won = (await post(client, f"/api/v1/market-bids/{rival_bid}/accept", w.customer)).json()
    assert won["status"] == "awarded"
    assert {b["status"] for b in won["bids"]} == {"accepted", "rejected"}
    o = (await client.get(obj, headers=w.customer)).json()
    assert o["contractor_org_id"] == rival_id and o["match_mode"] == "market"
    assert (await client.get(obj, headers=rival)).status_code == 200
    assert (await post(client, bids, w.boss, bid)).status_code == 409
    assert (
        await _put(client, f"{obj}/match-mode", w.customer, {"mode": "platform"})
    ).status_code == 409
    async with engine.begin() as conn:
        templates = set((await conn.execute(select(Notification.template))).scalars().all())
    assert {"market_bid", "market_won", "market_lost"} <= templates

    # Отзыв — после завершения; рейтинг попадает в каталог.
    review = f"{obj}/contractor-review"
    assert (await post(client, review, w.customer, {"rating": 5})).status_code == 409
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE objects SET status = 'completed' WHERE id = :id"), {"id": w.object_id}
        )
    assert (await post(client, review, rival, {"rating": 1})).status_code == 403
    r = await post(client, review, w.customer, {"rating": 5, "text": "Аккуратно и в срок"})
    assert r.status_code == 201, r.text
    assert (await post(client, review, w.customer, {"rating": 4})).status_code == 409
    card = (await client.get(f"/api/v1/contractors/{rival_id}", headers=w.customer)).json()
    assert card["rating"] == 5 and card["reviews"] == 1 and card["objects_done"] == 1
    assert card["review_list"][0]["text"] == "Аккуратно и в срок"

    # Сравнение режимов во вкладке «Пилот».
    other = await post(
        client, "/api/v1/objects", w.customer, {"address_text": "Москва, ул. Старая, д. 2"}
    )
    await client.put(
        f"/api/v1/objects/{other.json()['id']}/contractor",
        json={"contractor_org_id": w.org_id},
        headers={**admin, **idem()},
    )
    kpi = (await client.get("/api/v1/admin/kpi", headers=admin)).json()
    modes = {m["mode"]: m for m in kpi["modes"]}
    assert modes["market"]["objects"] == 1 and modes["market"]["with_contractor"] == 1
    assert modes["market"]["bids_per_task"] == 2 and modes["market"]["avg_rating"] == 5
    assert modes["market"]["reached_work"] == 1
    assert modes["platform"]["objects"] == 1 and modes["platform"]["contractor_pct"] == 100
    assert modes["platform"]["bids_per_task"] is None
    csv = (await client.get("/api/v1/admin/kpi.csv", headers=admin)).text
    assert "Маркетплейс" in csv and "Подбор платформой" in csv


async def test_close_and_withdraw(client: AsyncClient, admin: dict[str, str]) -> None:
    w = await build_world(client, admin, contractor=False)
    obj = f"/api/v1/objects/{w.object_id}"
    created = await post(
        client,
        "/api/v1/objects",
        w.customer,
        {"address_text": "Москва, ул. Лесная, д. 5", "match_mode": "market"},
    )
    assert created.json()["match_mode"] == "market"
    await _put(client, f"{obj}/match-mode", w.customer, {"mode": "market"})
    t = (
        await _put(
            client,
            f"{obj}/market-task",
            w.customer,
            {"title": "Косметический ремонт", "area_hint": "Москва, ЮАО"},
        )
    ).json()
    mine = (
        await post(
            client, f"/api/v1/market-tasks/{t['id']}/bids", w.boss, {"price_kop": 100, "days": 3}
        )
    ).json()["my_bid"]
    w2 = (await post(client, f"/api/v1/market-bids/{mine['id']}/withdraw", w.boss)).json()
    assert w2["my_bid"]["status"] == "withdrawn" and w2["bids_count"] == 0
    assert (
        await post(client, f"/api/v1/market-bids/{mine['id']}/accept", w.customer)
    ).status_code == 409
    # Переход на подбор платформой снимает заявку.
    await _put(client, f"{obj}/match-mode", w.customer, {"mode": "platform"})
    view = (await client.get(f"{obj}/market-task", headers=w.customer)).json()
    assert view["status"] == "closed"
    assert (await client.get("/api/v1/market/tasks", headers=w.boss)).json() == []
