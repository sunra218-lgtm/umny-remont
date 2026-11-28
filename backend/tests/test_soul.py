"""«Ремонт с душой»: дизайнер с финальным словом, наполнение предметами; куратор проекта."""

import uuid
from datetime import date, timedelta

from httpx import AsyncClient
from sqlalchemy import select, update

from app.core.db import get_session
from app.core.security import utcnow
from app.models import Notification, RepairObject, Stage
from app.models.enums import ObjectStatus, StageStatus
from app.modules.soul import curator
from tests.conftest import idem, login
from tests.scenario import build_world, post
from tests.test_field import media_dir, photo  # noqa: F401

DESIGNER = "+79160000031"


async def _put(client: AsyncClient, url: str, h: dict[str, str], body):  # type: ignore[no-untyped-def]
    return await client.put(url, json=body, headers={**h, **idem()})


async def _templates(engine) -> set[str]:  # type: ignore[no-untyped-def]
    async with engine.begin() as conn:
        return set((await conn.execute(select(Notification.template))).scalars().all())


async def test_soul_flow(  # type: ignore[no-untyped-def]
    client: AsyncClient,
    admin: dict[str, str],
    engine,
    media_dir,  # noqa: F811
) -> None:
    w = await build_world(client, admin, contractor=False)
    obj = f"/api/v1/objects/{w.object_id}"
    designer = await login(client, DESIGNER)
    designer_id = (await client.get("/api/v1/me", headers=designer)).json()["id"]

    # Дизайнер заполняет профиль; в каталоге — после проверки администратором.
    prof = {
        "display_name": "Анна Светлова",
        "city": "Москва",
        "styles": ["scandi", "japandi"],
        "price_m2_kop": 250_000,
        "about": "Тёплые интерьеры из натуральных материалов.",
    }
    assert (await _put(client, "/api/v1/designers/me", designer, prof)).status_code == 200
    assert (
        await _put(client, "/api/v1/designers/me", designer, {**prof, "styles": ["x"]})
    ).status_code == 422
    r = await client.post(
        "/api/v1/designers/me/photos",
        files={"photo": ("a.jpg", photo(5), "image/jpeg")},
        headers={**designer, **idem()},
    )
    assert r.status_code == 201 and len(r.json()["photos"]) == 1
    assert (await client.get(r.json()["photos"][0]["url"])).status_code == 200
    assert (await client.get("/api/v1/designers", headers=w.customer)).json() == []
    assert (
        await client.get(f"/api/v1/designers/{designer_id}", headers=w.customer)
    ).status_code == 404
    assert (
        await _put(client, f"/api/v1/designers/{designer_id}/verify", designer, {"verified": True})
    ).status_code == 403
    await _put(client, f"/api/v1/designers/{designer_id}/verify", admin, {"verified": True})
    cat = (await client.get("/api/v1/designers?style=japandi", headers=w.customer)).json()
    assert [d["user_id"] for d in cat] == [designer_id] and cat[0]["phone"] is None

    # Пригласить — когда включён «Ремонт с душой» и выбран стиль.
    invite = {"designer_id": designer_id, "message": "Хочется уюта и света"}
    assert (await post(client, f"{obj}/designer", w.customer, invite)).status_code == 409
    soul = (await _put(client, f"{obj}/soul", w.customer, {"soul": True})).json()
    assert soul["soul"] and soul["contract"] is None
    assert (await post(client, f"{obj}/designer", w.customer, invite)).status_code == 409
    from tests.test_design import _seed as seed_design

    await seed_design()
    await _put(client, f"{obj}/design", w.customer, {"style": "scandi"})
    r = await post(client, f"{obj}/designer", w.customer, invite)
    assert r.status_code == 201, r.text
    assert "designer_invited" in await _templates(engine)
    assert (await post(client, f"{obj}/designer", w.customer, invite)).status_code == 409

    # Дизайнер видит заявку без точного адреса и пока не видит объект.
    mine = (await client.get("/api/v1/designers/me/projects", headers=designer)).json()
    assert len(mine) == 1 and mine[0]["status"] == "invited"
    assert mine[0]["area_hint"] == "Москва, ул. Новая" and mine[0]["style"] == "scandi"
    cid = mine[0]["id"]
    assert (await client.get(f"{obj}/design", headers=designer)).status_code == 404
    offer = {"fee_kop": 13_500_000, "days": 21}
    assert (
        await post(client, f"/api/v1/designer-contracts/{cid}/offer", w.customer, offer)
    ).status_code == 404
    assert (
        await post(client, f"/api/v1/designer-contracts/{cid}/accept", w.customer)
    ).status_code == 409
    r = await post(client, f"/api/v1/designer-contracts/{cid}/offer", designer, offer)
    assert r.json()["status"] == "offered"
    assert (
        await post(client, f"/api/v1/designer-contracts/{cid}/accept", designer)
    ).status_code == 404
    soul = (await post(client, f"/api/v1/designer-contracts/{cid}/accept", w.customer)).json()
    assert soul["contract"]["status"] == "working"
    assert soul["contract"]["designer"]["phone"] == DESIGNER

    # Дизайнер работает: правит отделку, пишет концепцию, наполняет комнаты.
    p = (await client.get(f"{obj}/design", headers=designer)).json()
    assert p["can_edit"] and p["soul"]
    mats = (await client.get("/api/v1/design/materials?kind=parquet", headers=designer)).json()
    r = await _put(client, f"{obj}/design/rooms/r1/floor", designer, {"material_id": mats[0]["id"]})
    assert r.status_code == 200
    assert (
        await post(client, f"/api/v1/designer-contracts/{cid}/submit", designer)
    ).status_code == 422
    concept = {
        "concept": "Светлый скандинавский интерьер с тёплым деревом.",
        "room_notes": {"r1": "Диван у окна, плед, торшер для чтения."},
    }
    assert (
        await _put(client, f"/api/v1/designer-contracts/{cid}/concept", w.customer, concept)
    ).status_code == 403
    c = (await _put(client, f"/api/v1/designer-contracts/{cid}/concept", designer, concept)).json()
    assert c["room_notes"]["r1"].startswith("Диван")
    decor = await post(
        client,
        f"{obj}/design/decor",
        designer,
        {"room_key": "r1", "title": "Торшер с льняным абажуром", "price_kop": 1_800_000},
    )
    assert decor.status_code == 201 and decor.json()[0]["by_designer"]
    mine_decor = await post(
        client, f"{obj}/design/decor", w.customer, {"room_key": "r1", "title": "Бабушкино кресло"}
    )
    assert [d["by_designer"] for d in mine_decor.json()] == [True, False]
    assert (
        await post(client, f"{obj}/design/decor", designer, {"room_key": "zz", "title": "X1"})
    ).status_code == 404
    c = (await post(client, f"/api/v1/designer-contracts/{cid}/submit", designer)).json()
    assert c["status"] == "submitted" and c["rounds"] == 1

    # Заказчик утверждает — дизайн финальный, менять можно только через доработку.
    rv = f"/api/v1/designer-contracts/{cid}/review"
    soul = (await post(client, rv, w.customer, {"approve": True})).json()
    assert soul["contract"]["status"] == "approved" and soul["finalized_at"]
    r = await _put(client, f"{obj}/design/rooms/r1/floor", w.customer, {"material_id": None})
    assert r.status_code == 409 and r.json()["type"] == "/problems/design-final"
    assert (await _put(client, f"{obj}/design", designer, {"style": "loft"})).status_code == 409
    assert (
        await post(client, f"{obj}/design/decor", w.customer, {"room_key": "r1", "title": "Ваза"})
    ).status_code == 409
    assert (await post(client, rv, w.customer, {"approve": False})).status_code == 422
    soul = (
        await post(client, rv, w.customer, {"approve": False, "feedback": "Добавьте зелени"})
    ).json()
    assert soul["contract"]["status"] == "working" and soul["finalized_at"] is None
    assert (
        await _put(client, f"{obj}/design/rooms/r1/floor", designer, {"material_id": None})
    ).status_code == 200
    assert {"design_submitted", "design_approved", "design_feedback"} <= await _templates(engine)

    # Пока дизайнер работает, «душу» не выключить; отмена снимает доступ.
    assert (await _put(client, f"{obj}/soul", w.customer, {"soul": False})).status_code == 409
    await post(client, f"/api/v1/designer-contracts/{cid}/cancel", w.customer)
    assert (await client.get(f"{obj}/design", headers=designer)).status_code == 404
    assert (await _put(client, f"{obj}/soul", w.customer, {"soul": False})).json()["soul"] is False


async def test_curator(client: AsyncClient, admin: dict[str, str], engine) -> None:  # type: ignore[no-untyped-def]
    w = await build_world(client, admin)
    obj = f"/api/v1/objects/{w.object_id}"
    oid = uuid.UUID(w.object_id)
    stage_id = uuid.uuid4()
    async for db in get_session():
        await db.execute(
            update(RepairObject).where(RepairObject.id == oid).values(status=ObjectStatus.active)
        )
        db.add(
            Stage(
                id=stage_id,
                object_id=oid,
                seq=1,
                title="Электрика",
                stage_type="electric",
                work_budget_kop=0,
                material_budget_kop=0,
                planned_end=date.today() - timedelta(days=2),
                status=StageStatus.in_progress,
            )
        )
        await db.commit()

    # ИИ-куратор по умолчанию: опоздание этапа и нет фотоотчётов.
    later = utcnow() + timedelta(days=3)
    async for db in get_session():
        o = await db.get(RepairObject, oid)
        assert await curator.check_object(db, o, later) == 2
        await db.commit()
        assert await curator.check_object(db, o, later) == 0
        await db.commit()
    cur = (await client.get(f"{obj}/curator", headers=w.customer)).json()
    assert cur["kind"] == "ai" and cur["can_choose"] and not cur["can_assign"]
    assert {s["code"] for s in cur["signals"]} == {"no_photos", "stage_overdue"}
    assert "curator_alert" not in await _templates(engine)  # ИИ не шлёт СМС о предупреждениях

    # Заказчик просит человека — администратор получает СМС и назначает.
    cur = (await _put(client, f"{obj}/curator", w.customer, {"kind": "human"})).json()
    assert cur["kind"] == "human" and cur["curator"] is None
    assert "curator_alert" in await _templates(engine)
    bad = {"kind": "human", "user_id": w.user_ids["foreman"]}
    assert (await _put(client, f"{obj}/curator", admin, bad)).status_code == 422
    assert (
        await _put(
            client,
            f"{obj}/curator",
            w.customer,
            {"kind": "human", "user_id": w.user_ids["inspector"]},
        )
    ).status_code == 403
    cur = (
        await _put(
            client, f"{obj}/curator", admin, {"kind": "human", "user_id": w.user_ids["inspector"]}
        )
    ).json()
    assert cur["curator"]["id"] == w.user_ids["inspector"]
    assert "curator_assigned" in await _templates(engine)

    # Куратор видит объект, пишет заметку, закрывает сигнал.
    cur = (await client.get(f"{obj}/curator", headers=w.inspector)).json()
    assert cur["is_curator"]
    assert (
        await post(client, f"{obj}/curator/notes", w.foreman, {"text": "Привет"})
    ).status_code == 403
    cur = (
        await post(
            client, f"{obj}/curator/notes", w.inspector, {"text": "Был на объекте, всё по плану."}
        )
    ).json()
    assert cur["notes"][0]["by_human"]
    sig = next(s for s in cur["signals"] if s["code"] == "no_photos")
    cur = (await post(client, f"/api/v1/curator-signals/{sig['id']}/resolve", w.inspector)).json()
    assert all(s["code"] != "no_photos" for s in cur["signals"] if s["resolved_at"] is None)
    mine = (await client.get("/api/v1/curator/objects", headers=w.inspector)).json()
    assert [m["id"] for m in mine] == [w.object_id] and mine[0]["worst"] == "warn"

    # Этап приняли — сигнал об опоздании закрывается сам.
    async for db in get_session():
        await db.execute(
            update(Stage).where(Stage.id == stage_id).values(status=StageStatus.accepted)
        )
        await db.commit()
    cur = (await post(client, f"{obj}/curator/check", w.customer)).json()
    assert all(s["resolved_at"] for s in cur["signals"] if s["code"] == "stage_overdue")

    # Вернуться к ИИ-куратору: назначение человека снимается.
    cur = (await _put(client, f"{obj}/curator", w.customer, {"kind": "ai"})).json()
    assert cur["kind"] == "ai" and cur["curator"] is None
    assert (await client.get(f"{obj}/curator", headers=w.inspector)).status_code == 404
