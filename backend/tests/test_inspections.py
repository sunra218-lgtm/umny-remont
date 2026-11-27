"""Спринт 4: чек-листы, ход этапа, выезд технадзора и проверка фото."""

from httpx import AsyncClient
from sqlalchemy import text

from tests.conftest import idem
from tests.scenario import build_world, post
from tests.test_field import foreman_on_phone, media_dir, photo, upload  # noqa: F401

TEMPLATE = {
    "stage_type": "electric",
    "title": "Электрика",
    "items": [
        {"title": "Трассы кабеля", "required_angle": "Стена целиком"},
        {"title": "Щит", "required_angle": None},
        {"title": "Замер изоляции", "photo_required": False},
    ],
}


async def test_checklist_templates_admin(client: AsyncClient, admin: dict[str, str]) -> None:
    w = await build_world(client, admin)
    url = "/api/v1/checklist-templates"
    assert (await post(client, url, w.boss, TEMPLATE)).status_code == 403
    bad = await post(client, url, admin, {**TEMPLATE, "stage_type": "magic"})
    assert bad.status_code == 422
    t = (await post(client, url, admin, TEMPLATE)).json()
    assert [i["seq"] for i in t["items"]] == [1, 2, 3]
    assert (await post(client, url, admin, TEMPLATE)).status_code == 409
    items = t["items"]
    body = {
        **TEMPLATE,
        "items": [
            {"id": items[1]["id"], "title": "Электрощит"},
            {"id": items[0]["id"], "title": items[0]["title"]},
        ],
    }
    r = await client.put(f"{url}/{t['id']}", json=body, headers={**admin, **idem()})
    assert r.status_code == 200, r.text
    assert [i["title"] for i in r.json()["items"]] == ["Электрощит", "Трассы кабеля"]
    listed = (await client.get(url, headers=w.foreman)).json()
    assert listed[0]["stage_type_title"] == "Электрика"
    types = (await client.get("/api/v1/stage-types", headers=w.foreman)).json()
    assert {"code": "electric", "title": "Электрика"} in types
    r = await client.delete(f"{url}/{t['id']}", headers={**admin, **idem()})
    assert r.status_code == 204


async def test_stage_flow_with_stop_point(
    client: AsyncClient,
    admin: dict[str, str],
    engine,  # type: ignore[no-untyped-def]
) -> None:
    w = await build_world(client, admin)
    template = (await post(client, "/api/v1/checklist-templates", admin, TEMPLATE)).json()
    stage = (
        await post(
            client,
            f"/api/v1/objects/{w.object_id}/stages",
            w.estimator,
            {"title": "Электрика", "stage_type": "electric", "is_stop_point": True},
        )
    ).json()
    sid = stage["id"]
    lite, device, _ = await foreman_on_phone(client, w)

    # Без резерва работать нельзя, резерв — только после утверждения сметы.
    assert (await post(client, f"/api/v1/stages/{sid}/start", lite)).status_code == 409
    assert (await post(client, f"/api/v1/stages/{sid}/mark-funded", w.boss)).status_code == 403
    assert (await post(client, f"/api/v1/stages/{sid}/mark-funded", admin)).status_code == 409
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE objects SET status = 'contracting' WHERE id = :id"), {"id": w.object_id}
        )
    funded = await post(client, f"/api/v1/stages/{sid}/mark-funded", admin)
    assert funded.json()["status"] == "ready"
    obj = (await client.get(f"/api/v1/objects/{w.object_id}", headers=w.customer)).json()
    assert obj["status"] == "active"
    assert (await post(client, f"/api/v1/stages/{sid}/start", w.customer)).status_code == 403
    started = await post(client, f"/api/v1/stages/{sid}/start", lite)
    assert started.json()["status"] == "in_progress"

    # Фото по пункту чек-листа; пункт чужого шаблона не подходит.
    first_item = template["items"][0]["id"]
    ch = (await post(client, f"/api/v1/stages/{sid}/capture-challenges", lite)).json()
    shot = await upload(
        client,
        lite,
        device,
        sid,
        photo(11),
        challenge=ch,
        extra={"checklist_item_id": first_item},
    )
    assert shot.status_code == 201, shot.text
    wrong = await upload(
        client, lite, device, sid, photo(12), extra={"checklist_item_id": w.object_id}
    )
    assert wrong.status_code == 422
    checklist = (await client.get(f"/api/v1/stages/{sid}/checklist", headers=w.customer)).json()
    assert [i["photos"] for i in checklist["items"]] == [1, 0, 0]
    assert checklist["missing"] == [template["items"][1]["id"]]

    submitted = await post(client, f"/api/v1/stages/{sid}/submit", lite)
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["stage"]["status"] == "inspection"
    assert submitted.json()["missing"] == ["Щит"]

    # Выезд технадзора.
    url = f"/api/v1/stages/{sid}/inspections"
    body = {"inspector_id": w.user_ids["inspector"], "scheduled_at": "2027-01-15T10:00:00+03:00"}
    assert (await post(client, url, w.boss, body)).status_code == 403
    not_insp = {"inspector_id": w.user_ids["foreman"]}
    assert (await post(client, url, admin, not_insp)).status_code == 422
    insp = (await post(client, url, admin, body)).json()
    assert (await post(client, url, admin, body)).status_code == 409
    inspector = w.inspector
    mine = (await client.get("/api/v1/inspections", headers=inspector)).json()
    assert [i["id"] for i in mine["items"]] == [insp["id"]]
    assert mine["items"][0]["address"].startswith("Москва")
    result_url = f"/api/v1/inspections/{insp['id']}/result"
    assert (await post(client, result_url, admin, {"result": "passed"})).status_code == 403
    no_text = await post(client, result_url, inspector, {"result": "remarks"})
    assert no_text.status_code == 422

    # Фото проверяет инженер: отклонённое не засчитывается в чек-лист.
    media_id = shot.json()["id"]
    review_url = f"/api/v1/media/{media_id}/review"
    assert (await post(client, review_url, w.boss, {"status": "approved"})).status_code == 403
    assert (await post(client, review_url, inspector, {"status": "rejected"})).status_code == 422
    rejected = await post(
        client, review_url, inspector, {"status": "rejected", "comment": "Не видно штроб"}
    )
    assert rejected.json()["review_status"] == "rejected"
    feed = (await client.get(f"/api/v1/objects/{w.object_id}/feed", headers=w.customer)).json()
    assert feed["items"][0]["review_comment"] == "Не видно штроб"
    assert feed["items"][0]["checklist_item_title"] == "Трассы кабеля"
    checklist = (await client.get(f"/api/v1/stages/{sid}/checklist", headers=w.foreman)).json()
    assert len(checklist["missing"]) == 2

    remarks = await post(
        client, result_url, inspector, {"result": "remarks", "remarks": "Переснять трассы"}
    )
    assert remarks.status_code == 200, remarks.text
    assert remarks.json()["stage_status"] == "in_progress"
    assert remarks.json()["signed_at"]
    assert (await post(client, result_url, inspector, {"result": "passed"})).status_code == 409

    # Повторная сдача и повторный выезд — теперь проверка пройдена.
    await post(client, f"/api/v1/stages/{sid}/submit", lite)
    insp2 = (await post(client, url, admin, body)).json()
    passed = await post(
        client, f"/api/v1/inspections/{insp2['id']}/result", inspector, {"result": "passed"}
    )
    assert passed.json()["stage_status"] == "acceptance"
    history = (await client.get(url, headers=w.customer)).json()
    assert [h["result"] for h in history] == ["passed", "remarks"]
    log = (await client.get("/api/v1/admin/audit-logs?entity_type=stage", headers=admin)).json()
    assert any(e["action"] == "stage.inspected" for e in log["items"])


async def test_stage_without_stop_point(
    client: AsyncClient,
    admin: dict[str, str],
    engine,  # type: ignore[no-untyped-def]
) -> None:
    w = await build_world(client, admin)
    stage = (
        await post(
            client, f"/api/v1/objects/{w.object_id}/stages", w.estimator, {"title": "Стяжка"}
        )
    ).json()
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE objects SET status = 'active' WHERE id = :id"), {"id": w.object_id}
        )
    sid = stage["id"]
    await post(client, f"/api/v1/stages/{sid}/mark-funded", admin)
    await post(client, f"/api/v1/stages/{sid}/start", w.boss)
    out = (await post(client, f"/api/v1/stages/{sid}/submit", w.boss)).json()
    assert out == {**out, "missing": []}
    assert out["stage"]["status"] == "acceptance"
    no_stop = await post(
        client,
        f"/api/v1/stages/{sid}/inspections",
        admin,
        {"inspector_id": w.user_ids["inspector"]},
    )
    assert no_stop.status_code == 409
    # Этап в работе больше нельзя редактировать.
    r = await client.patch(
        f"/api/v1/stages/{sid}", json={"title": "Стяжка пола"}, headers={**w.estimator, **idem()}
    )
    assert r.status_code == 409


async def test_stage_card(client: AsyncClient, admin: dict[str, str]) -> None:
    w = await build_world(client, admin)
    stage = (
        await post(
            client, f"/api/v1/objects/{w.object_id}/stages", w.estimator, {"title": "Стяжка"}
        )
    ).json()
    card = (await client.get(f"/api/v1/stages/{stage['id']}", headers=w.customer)).json()
    assert card["is_customer"] and not card["can_work"]
    assert (
        await client.get(f"/api/v1/stages/{stage['id']}", headers=w.stranger)
    ).status_code == 404
