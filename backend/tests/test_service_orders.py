from httpx import AsyncClient

from tests.scenario import build_world, post


async def test_survey_lifecycle_moves_object_status(
    client: AsyncClient, admin: dict[str, str]
) -> None:
    w = await build_world(client, admin)
    url = f"/api/v1/objects/{w.object_id}/service-orders"
    assert (await post(client, url, w.boss, {"type": "survey"})).status_code == 403
    order = await post(
        client,
        url,
        w.customer,
        {"type": "survey", "preferred_at": "2026-12-05T10:00:00+03:00", "comment": "Домофон 10"},
    )
    assert order.status_code == 201, order.text
    order = order.json()
    assert order["price_kop"] == 740_000
    assert "05.12.2026" in order["customer_comment"]
    obj = (await client.get(f"/api/v1/objects/{w.object_id}", headers=w.customer)).json()
    assert obj["status"] == "survey"
    assert (await post(client, url, w.customer, {"type": "survey"})).status_code == 409

    oid = order["id"]
    # Нельзя назначить не-инженера.
    bad = await post(
        client,
        f"/api/v1/service-orders/{oid}/schedule",
        admin,
        {"inspector_id": w.user_ids["foreman"], "slot_at": "2026-12-05T10:00:00+03:00"},
    )
    assert bad.status_code == 422
    resp = await post(
        client,
        f"/api/v1/service-orders/{oid}/schedule",
        admin,
        {
            "inspector_id": w.user_ids["inspector"],
            "slot_at": "2026-12-05T10:00:00+03:00",
            "price_kop": 650_000,
        },
    )
    assert resp.json()["status"] == "scheduled"
    assert resp.json()["price_kop"] == 650_000
    # Инженер теперь видит объект и свой выезд.
    mine = (await client.get("/api/v1/service-orders", headers=w.inspector)).json()
    assert [o["id"] for o in mine] == [oid]
    # Завершить без оплаты нельзя.
    done = {"result_notes": "Обмеры сделаны, план приложен"}
    assert (
        await post(client, f"/api/v1/service-orders/{oid}/complete", w.inspector, done)
    ).status_code == 409
    assert (
        await post(client, f"/api/v1/service-orders/{oid}/mark-paid", w.inspector)
    ).status_code == 403
    resp = await post(client, f"/api/v1/service-orders/{oid}/mark-paid", admin)
    assert resp.json()["status"] == "paid" and resp.json()["paid_at"]
    resp = await post(client, f"/api/v1/service-orders/{oid}/complete", w.inspector, done)
    assert resp.json()["status"] == "completed"
    obj = (await client.get(f"/api/v1/objects/{w.object_id}", headers=w.customer)).json()
    assert obj["status"] == "estimating"
    # Повторный замер не нужен.
    assert (await post(client, url, w.customer, {"type": "survey"})).status_code == 409


async def test_customer_cancels_unpaid_but_not_paid(
    client: AsyncClient, admin: dict[str, str]
) -> None:
    w = await build_world(client, admin)
    url = f"/api/v1/objects/{w.object_id}/service-orders"
    first = (await post(client, url, w.customer, {"type": "acceptance"})).json()
    resp = await post(client, f"/api/v1/service-orders/{first['id']}/cancel", w.customer)
    assert resp.json()["status"] == "cancelled"
    second = (await post(client, url, w.customer, {"type": "acceptance"})).json()
    await post(
        client,
        f"/api/v1/service-orders/{second['id']}/schedule",
        admin,
        {"inspector_id": w.user_ids["inspector"], "slot_at": "2026-12-06T12:00:00+03:00"},
    )
    await post(client, f"/api/v1/service-orders/{second['id']}/mark-paid", admin)
    resp = await post(client, f"/api/v1/service-orders/{second['id']}/cancel", w.customer)
    assert resp.status_code == 409
    listing = (await client.get(url, headers=w.customer)).json()
    assert [o["status"] for o in listing] == ["paid", "cancelled"]
