import io

from httpx import AsyncClient
from openpyxl import Workbook

from tests.conftest import idem
from tests.scenario import World, build_world, post


async def _to_estimating(client: AsyncClient, w: World) -> None:
    order = (
        await post(
            client, f"/api/v1/objects/{w.object_id}/service-orders", w.customer, {"type": "survey"}
        )
    ).json()
    await post(
        client,
        f"/api/v1/service-orders/{order['id']}/schedule",
        w.admin,
        {"inspector_id": w.user_ids["inspector"], "slot_at": "2026-12-05T10:00:00+03:00"},
    )
    await post(client, f"/api/v1/service-orders/{order['id']}/mark-paid", w.admin)
    await post(
        client,
        f"/api/v1/service-orders/{order['id']}/complete",
        w.inspector,
        {"result_notes": "Обмеры сделаны"},
    )


def _xlsx(rows: list[list[object]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(["Смета на ремонт"])  # строка-шапка до заголовков
    ws.append(["Этап", "Помещение", "Тип", "Наименование", "Ед. изм.", "Количество", "Цена, ₽"])
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def _import(
    client: AsyncClient,
    headers: dict[str, str],
    estimate_id: str,
    content: bytes,
    mode: str = "replace",
):  # type: ignore[no-untyped-def]
    return await client.post(
        f"/api/v1/estimates/{estimate_id}/import-xlsx?mode={mode}",
        files={"file": ("smeta.xlsx", content, "application/octet-stream")},
        headers={**headers, **idem()},
    )


async def test_preliminary_estimate_items_and_totals(
    client: AsyncClient, admin: dict[str, str]
) -> None:
    w = await build_world(client, admin)
    url = f"/api/v1/objects/{w.object_id}/estimates"
    assert (await post(client, url, w.customer, {"type": "preliminary"})).status_code == 403
    # Полная смета до замера недоступна.
    assert (await post(client, url, w.estimator, {"type": "full"})).status_code == 409
    est = (await post(client, url, w.estimator, {"type": "preliminary"})).json()
    assert est["version"] == 1 and est["status"] == "draft" and est["can_edit"] is True

    items_url = f"/api/v1/estimates/{est['id']}/items"
    r = await post(
        client,
        items_url,
        w.estimator,
        {
            "category": "work",
            "title": "Штукатурка стен",
            "unit": "м²",
            "qty": "12.345",
            "unit_price_kop": 75_050,
        },
    )
    assert r.status_code == 201, r.text
    # 12.345 × 750.50 ₽ = 9264.9225 ₽ → 926 492 коп. (округление до копейки)
    assert r.json()["total_work_kop"] == 926_492

    catalog = (await client.get("/api/v1/price-items?q=Кабель", headers=w.estimator)).json()
    assert catalog == []  # в тестах справочник пуст — добавим позицию
    pi = await post(
        client,
        "/api/v1/price-items",
        admin,
        {
            "category": "material",
            "stage_type": "electric",
            "title": "Кабель ВВГнг 3×2,5",
            "unit": "м.п.",
            "unit_price_kop": 9500,
        },
    )
    assert pi.status_code == 201
    r = await post(client, items_url, w.estimator, {"price_item_id": pi.json()["id"], "qty": 100})
    body = r.json()
    assert body["total_material_kop"] == 950_000
    cable = next(i for i in body["items"] if i["category"] == "material")
    assert cable["title"] == "Кабель ВВГнг 3×2,5" and cable["price_item_id"]

    r = await client.patch(
        f"/api/v1/estimates/{est['id']}",
        json={"reserve_kop": 100_000},
        headers={**w.estimator, **idem()},
    )
    assert r.json()["total_kop"] == 926_492 + 950_000 + 100_000
    r = await client.patch(
        f"/api/v1/estimate-items/{cable['id']}",
        json={"qty": "50"},
        headers={**w.estimator, **idem()},
    )
    assert r.json()["total_material_kop"] == 475_000

    # Заказчик не видит черновик.
    assert (
        await client.get(f"/api/v1/estimates/{est['id']}", headers=w.customer)
    ).status_code == 404
    assert (await client.get(url, headers=w.customer)).json() == []

    r = await post(client, f"/api/v1/estimates/{est['id']}/submit", w.estimator)
    assert r.json()["status"] == "submitted" and r.json()["can_edit"] is False
    assert (
        await post(client, items_url, w.estimator, {"price_item_id": pi.json()["id"], "qty": 1})
    ).status_code == 409
    seen = (await client.get(f"/api/v1/estimates/{est['id']}", headers=w.customer)).json()
    assert seen["can_approve"] is True and seen["can_edit"] is False


async def test_versions_copy_items(client: AsyncClient, admin: dict[str, str]) -> None:
    w = await build_world(client, admin)
    url = f"/api/v1/objects/{w.object_id}/estimates"
    v1 = (await post(client, url, w.estimator, {"type": "preliminary"})).json()
    await post(
        client,
        f"/api/v1/estimates/{v1['id']}/items",
        w.estimator,
        {
            "category": "work",
            "title": "Демонтаж",
            "unit": "м²",
            "qty": 10,
            "unit_price_kop": 35_000,
        },
    )
    # Пока черновик не отправлен, второй черновик не создаётся.
    assert (await post(client, url, w.estimator, {"type": "preliminary"})).status_code == 409
    await post(client, f"/api/v1/estimates/{v1['id']}/submit", w.estimator)
    v2 = (await post(client, url, w.estimator, {"type": "preliminary"})).json()
    assert v2["version"] == 2 and len(v2["items"]) == 1 and v2["total_work_kop"] == 350_000
    versions = (
        await client.get(f"/api/v1/estimates/{v2['id']}/versions", headers=w.estimator)
    ).json()
    assert [v["version"] for v in versions] == [2, 1]
    # Старую версию заказчик утвердить уже не может.
    resp = await post(client, f"/api/v1/estimates/{v1['id']}/approve/request", w.customer)
    assert resp.status_code == 409


async def test_full_estimate_approval_with_sms_signature(
    client: AsyncClient, admin: dict[str, str]
) -> None:
    w = await build_world(client, admin)
    await _to_estimating(client, w)
    url = f"/api/v1/objects/{w.object_id}/estimates"
    est = (await post(client, url, w.boss, {"type": "full", "empty": True})).json()
    content = _xlsx(
        [
            ["Электрика", "Кухня", "работа", "Прокладка кабеля", "м.п.", 45, 320],
            ["Электрика", "Кухня", "материал", "Кабель ВВГнг-LS 3×2,5", "м.п.", 50, "95,50"],
            ["Стяжка", "", "Работы", "Полусухая стяжка", "м²", "62,5", "850"],
            [None, None, None, None, None, None, None],
        ]
    )
    r = await _import(client, w.estimator, est["id"], content)
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == 3 and r.json()["stages_created"] == 2
    est = r.json()["estimate"]
    assert est["total_work_kop"] == 45 * 32_000 + 6_250 * 850  # 1 440 000 + 5 312 500
    assert est["total_material_kop"] == 477_500

    r = await post(client, f"/api/v1/estimates/{est['id']}/submit", w.estimator)
    assert r.status_code == 200
    assert (
        await post(client, f"/api/v1/estimates/{est['id']}/approve/request", w.boss)
    ).status_code == 403

    req = (await post(client, f"/api/v1/estimates/{est['id']}/approve/request", w.customer)).json()
    wrong = "000000" if req["debug_code"] != "000000" else "111111"
    r = await post(
        client,
        f"/api/v1/estimates/{est['id']}/approve",
        w.customer,
        {"signature_id": req["signature_id"], "code": wrong},
    )
    assert r.status_code == 400
    r = await post(
        client,
        f"/api/v1/estimates/{est['id']}/approve",
        w.customer,
        {"signature_id": req["signature_id"], "code": req["debug_code"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"

    obj = (await client.get(f"/api/v1/objects/{w.object_id}", headers=w.customer)).json()
    assert obj["status"] == "contracting"
    stages = (await client.get(f"/api/v1/objects/{w.object_id}/stages", headers=w.customer)).json()
    budgets = {s["title"]: (s["work_budget_kop"], s["material_budget_kop"]) for s in stages}
    assert budgets == {"Электрика": (1_440_000, 477_500), "Стяжка": (5_312_500, 0)}
    # Повторно утвердить нельзя.
    assert (
        await post(client, f"/api/v1/estimates/{est['id']}/approve/request", w.customer)
    ).status_code == 409

    logs = (
        await client.get(f"/api/v1/admin/audit-logs?entity_id={est['id']}", headers=admin)
    ).json()
    approved = next(e for e in logs["items"] if e["action"] == "estimate.approved")
    assert len(approved["new_val"]["document_hash"]) == 64


async def test_full_estimate_requires_stage_for_each_item(
    client: AsyncClient, admin: dict[str, str]
) -> None:
    w = await build_world(client, admin)
    await _to_estimating(client, w)
    est = (
        await post(
            client,
            f"/api/v1/objects/{w.object_id}/estimates",
            w.estimator,
            {"type": "full", "empty": True},
        )
    ).json()
    await post(
        client,
        f"/api/v1/estimates/{est['id']}/items",
        w.estimator,
        {
            "category": "work",
            "title": "Уборка",
            "unit": "компл",
            "qty": 1,
            "unit_price_kop": 500_000,
        },
    )
    r = await post(client, f"/api/v1/estimates/{est['id']}/submit", w.estimator)
    assert r.status_code == 409
    assert r.json()["type"] == "/problems/items-without-stage"


async def test_import_reports_row_errors_and_changes_nothing(
    client: AsyncClient, admin: dict[str, str]
) -> None:
    w = await build_world(client, admin)
    est = (
        await post(
            client, f"/api/v1/objects/{w.object_id}/estimates", w.estimator, {"type": "preliminary"}
        )
    ).json()
    content = _xlsx(
        [
            ["", "", "работа", "Нормальная строка", "шт", 1, 100],
            ["", "", "непонятно", "Плохой тип", "шт", 1, 100],
            ["", "", "работа", "Без количества", "шт", "много", 100],
        ]
    )
    r = await _import(client, w.estimator, est["id"], content)
    assert r.status_code == 422
    rows = r.json()["rows"]
    assert [e["row"] for e in rows] == [4, 5]
    same = (await client.get(f"/api/v1/estimates/{est['id']}", headers=w.estimator)).json()
    assert same["items"] == []
    r = await _import(client, w.estimator, est["id"], b"not an excel file")
    assert r.status_code == 422


async def test_import_template_downloads(client: AsyncClient) -> None:
    r = await client.get("/api/v1/estimates/import-template.xlsx")
    assert r.status_code == 200
    assert r.content[:2] == b"PK"
