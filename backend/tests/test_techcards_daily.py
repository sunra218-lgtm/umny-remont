"""Техкарты с допусками, чек-лист из шагов, отметки технадзора, дневной план и приёмка дня."""

from datetime import timedelta

from httpx import AsyncClient
from sqlalchemy import select, text

from app.core.db import get_session
from app.domain.tech_cards import TECH_CARDS
from app.models import ChecklistItem, ChecklistTemplate, Notification
from app.modules.daily.router import today
from app.modules.techcards.service import seed_tech_cards
from tests.conftest import idem
from tests.scenario import build_world, fund_stage, post


async def _seed() -> int:
    async for db in get_session():
        return await seed_tech_cards(db)
    return 0


async def _put(client: AsyncClient, url: str, headers: dict[str, str], body):  # type: ignore[no-untyped-def]
    return await client.put(url, json=body, headers={**headers, **idem()})


async def test_seed_builds_checklists(client: AsyncClient, admin: dict[str, str], engine) -> None:  # type: ignore[no-untyped-def]
    # Старый пункт чек-листа с тем же названием, что шаг техкарты, сохраняется (на него
    # могут ссылаться фото), лишний неиспользуемый — удаляется.
    async with engine.begin() as conn:
        tid = (
            await conn.execute(
                text(
                    "INSERT INTO checklist_templates (id, stage_type, title) "
                    "VALUES (gen_random_uuid(), 'screed', 'Стяжка') RETURNING id"
                )
            )
        ).scalar()
        await conn.execute(
            text(
                "INSERT INTO checklist_items (id, template_id, seq, title, photo_required) VALUES "
                "(gen_random_uuid(), :t, 1, 'Заливка или укладка', true), "
                "(gen_random_uuid(), :t, 2, 'Лишний пункт', true)"
            ),
            {"t": tid},
        )
        old_id = (
            await conn.execute(
                text("SELECT id FROM checklist_items WHERE title = 'Заливка или укладка'")
            )
        ).scalar()

    assert await _seed() == len(TECH_CARDS)
    assert await _seed() == 0
    async for db in get_session():
        templates = list((await db.execute(select(ChecklistTemplate))).scalars())
        assert {t.stage_type for t in templates} == {c["stage_type"] for c in TECH_CARDS}
        screed = next(t for t in templates if t.stage_type == "screed")
        items = list(
            (
                await db.execute(
                    select(ChecklistItem)
                    .where(ChecklistItem.template_id == screed.id)
                    .order_by(ChecklistItem.seq)
                )
            ).scalars()
        )
        assert [i.step_key for i in items] == ["base", "damper", "screed"]
        assert items[2].id == old_id and items[2].required_angle.startswith("Рейка 2 м")

    w = await build_world(client, admin)
    cards = (await client.get("/api/v1/tech-cards", headers=w.boss)).json()
    assert len(cards) == len(TECH_CARDS) and cards[0]["stage_type"] == "demolition"
    assert all(c["verified_at"] is None for c in cards)
    electric = (await client.get("/api/v1/tech-cards/electric", headers=w.customer)).json()
    assert any("0,5 МОм" in (s["tolerance"] or "") for s in electric["steps"])
    assert electric["norms"][0].startswith("ПУЭ")
    assert (await client.get("/api/v1/tech-cards/magic", headers=w.customer)).status_code == 404


async def test_edit_and_verify(client: AsyncClient, admin: dict[str, str]) -> None:
    await _seed()
    w = await build_world(client, admin)
    url = "/api/v1/tech-cards/doors"
    card = (await client.get(url, headers=admin)).json()
    body = {k: card[k] for k in ("title", "summary", "norms", "steps", "materials")}
    body["steps"] = [
        *body["steps"],
        {"key": "trim", "title": "Наличники", "how": "На клей и гвозди", "photo": "Дверь целиком"},
    ]
    assert (await _put(client, url, w.boss, body)).status_code == 403
    bad = {**body, "steps": [*body["steps"], body["steps"][0]]}
    assert (await _put(client, url, admin, bad)).status_code == 422
    bad_mat = {
        **body,
        "materials": [
            {"key": "x", "name": "Клей", "rate": 1, "per": "м²", "unit": "кг", "per_mm": True}
        ],
    }
    assert (await _put(client, url, admin, bad_mat)).status_code == 422
    assert (await post(client, f"{url}/verify", w.boss)).status_code == 403
    v = await post(client, f"{url}/verify", w.inspector)  # инженер технадзора платформы
    assert v.status_code == 200 and v.json()["verified_at"]
    r = await _put(client, url, admin, body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["version"] == 2 and out["verified_at"] is None  # после правки — снова на проверку
    templates = (await client.get("/api/v1/checklist-templates", headers=admin)).json()
    doors = next(t for t in templates if t["stage_type"] == "doors")
    assert [i["title"] for i in doors["items"]] == ["Коробка", "Полотно и фурнитура", "Наличники"]
    created = await _put(client, "/api/v1/tech-cards/unknown", admin, body)
    assert created.status_code == 404


async def test_stop_point_checks(client: AsyncClient, admin: dict[str, str], engine) -> None:  # type: ignore[no-untyped-def]
    await _seed()
    w = await build_world(client, admin)
    stage = (
        await post(
            client,
            f"/api/v1/objects/{w.object_id}/stages",
            w.estimator,
            {"title": "Электрика", "stage_type": "electric", "is_stop_point": True},
        )
    ).json()
    sid = stage["id"]
    card = (await client.get(f"/api/v1/stages/{sid}/tech-card", headers=w.customer)).json()
    assert card["title"] == "Электрика"
    keys = [s["key"] for s in card["steps"] if s["inspector"]]
    assert "insulation" in keys
    await fund_stage(client, engine, w, sid)
    await post(client, f"/api/v1/stages/{sid}/start", w.boss)
    await post(client, f"/api/v1/stages/{sid}/submit", w.boss)
    insp = (
        await post(
            client,
            f"/api/v1/stages/{sid}/inspections",
            admin,
            {"inspector_id": w.user_ids["inspector"]},
        )
    ).json()
    url = f"/api/v1/inspections/{insp['id']}/result"
    unknown = await post(
        client, url, w.inspector, {"result": "passed", "checks": [{"key": "magic", "ok": True}]}
    )
    assert unknown.status_code == 422
    checks = [{"key": k, "ok": k != "insulation"} for k in keys]
    failed = await post(client, url, w.inspector, {"result": "passed", "checks": checks})
    assert failed.status_code == 422 and "Замер сопротивления" in failed.json()["detail"]
    r = await post(
        client,
        url,
        w.inspector,
        {"result": "remarks", "remarks": "Нет протокола замера", "checks": checks},
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["stage_status"] == "in_progress"
    assert {c["key"]: c["ok"] for c in out["checks"]}["insulation"] is False
    assert out["checks"][0]["title"]

    # Этап без техкарты — карточки «как должно быть» нет.
    plain = (
        await post(
            client, f"/api/v1/objects/{w.object_id}/stages", w.estimator, {"title": "Прочее"}
        )
    ).json()
    assert (
        await client.get(f"/api/v1/stages/{plain['id']}/tech-card", headers=w.customer)
    ).json() is None


async def test_daily_plan(client: AsyncClient, admin: dict[str, str], engine) -> None:  # type: ignore[no-untyped-def]
    w = await build_world(client, admin)
    stage = (
        await post(client, f"/api/v1/objects/{w.object_id}/stages", w.estimator, {"title": "Стены"})
    ).json()
    day = today()
    url = f"/api/v1/objects/{w.object_id}/daily-plans/{day.isoformat()}"
    body = {
        "items": [{"title": "Штукатурка кухни", "stage_id": stage["id"]}, {"title": "Вынос мусора"}]
    }
    assert (await _put(client, url, w.boss, body)).status_code == 409  # объект ещё не в работе
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE objects SET status = 'active' WHERE id = :id"), {"id": w.object_id}
        )
    assert (await _put(client, url, w.customer, body)).status_code == 403
    far = f"/api/v1/objects/{w.object_id}/daily-plans/{(day + timedelta(days=30)).isoformat()}"
    assert (await _put(client, far, w.boss, body)).status_code == 422
    alien = {"items": [{"title": "Чужое", "stage_id": w.object_id}]}
    assert (await _put(client, url, w.boss, alien)).status_code == 422
    plan = (await _put(client, url, w.boss, body)).json()
    assert plan["status"] == "planned" and plan["can_edit"] and not plan["can_review"]
    assert plan["items"][0]["stage_title"] == "Стены"
    plan = (await _put(client, url, w.boss, {"items": body["items"][:1]})).json()
    assert len(plan["items"]) == 1

    # Нельзя принять день, пока бригада не сдала его; завтрашний день сдать нельзя.
    review = f"/api/v1/daily-plans/{plan['id']}/review"
    assert (await post(client, review, w.customer, {"accepted": True})).status_code == 409
    tomorrow = (
        await _put(
            client,
            f"/api/v1/objects/{w.object_id}/daily-plans/{(day + timedelta(days=1)).isoformat()}",
            w.boss,
            body,
        )
    ).json()
    early = await post(
        client, f"/api/v1/daily-plans/{tomorrow['id']}/report", w.boss, {"items": []}
    )
    assert early.status_code == 409

    report = f"/api/v1/daily-plans/{plan['id']}/report"
    done = {
        "items": [{"id": plan["items"][0]["id"], "done": True, "note": "Кухня готова"}],
        "note": "Без простоев",
    }
    assert (await post(client, report, w.customer, done)).status_code == 403
    reported = (await post(client, report, w.boss, done)).json()
    assert reported["status"] == "reported" and reported["items"][0]["done"]
    assert (await _put(client, url, w.boss, body)).status_code == 409  # день сдан — план закрыт
    async with engine.begin() as conn:
        sent = (
            await conn.execute(
                select(Notification.template, Notification.user_id).where(
                    Notification.template == "day_reported"
                )
            )
        ).all()
    assert len(sent) == 1  # заказчику; инженер на объект не назначен

    # Заказчик возвращает день с замечанием, бригада досдаёт, заказчик принимает.
    assert (await post(client, review, w.boss, {"accepted": True})).status_code == 403
    assert (await post(client, review, w.customer, {"accepted": False})).status_code == 422
    returned = (
        await post(client, review, w.customer, {"accepted": False, "note": "Мусор в коридоре"})
    ).json()
    assert returned["status"] == "returned" and returned["review_note"] == "Мусор в коридоре"
    await post(client, report, w.boss, done)
    accepted = (await post(client, review, w.customer, {"accepted": True})).json()
    assert accepted["status"] == "accepted"
    plans = (
        await client.get(f"/api/v1/objects/{w.object_id}/daily-plans", headers=w.customer)
    ).json()
    assert [p["status"] for p in plans] == ["planned", "accepted"]
