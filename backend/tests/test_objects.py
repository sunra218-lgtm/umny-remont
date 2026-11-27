from httpx import AsyncClient

from tests.conftest import idem
from tests.scenario import CUSTOMER, build_world, post


async def test_customer_creates_and_sees_object(client: AsyncClient, admin: dict[str, str]) -> None:
    w = await build_world(client, admin)
    obj = (await client.get(f"/api/v1/objects/{w.object_id}", headers=w.customer)).json()
    assert obj["status"] == "draft"
    assert obj["area_m2"] == "54.30"
    assert obj["contractor_name"] == "ООО «Ремонт»"
    assert obj["access"]["is_customer"] is True
    assert obj["access"]["can_edit_estimates"] is False
    listing = (await client.get("/api/v1/objects", headers=w.customer)).json()
    assert [o["id"] for o in listing["items"]] == [w.object_id]


async def test_visibility(client: AsyncClient, admin: dict[str, str]) -> None:
    w = await build_world(client, admin)
    for headers in (w.boss, w.estimator, w.admin):
        assert (
            await client.get(f"/api/v1/objects/{w.object_id}", headers=headers)
        ).status_code == 200
    # Посторонний и инженер без назначения объект не видят.
    for headers in (w.stranger, w.inspector):
        assert (
            await client.get(f"/api/v1/objects/{w.object_id}", headers=headers)
        ).status_code == 404
        assert (await client.get("/api/v1/objects", headers=headers)).json()["items"] == []


async def test_admin_creates_object_for_customer(
    client: AsyncClient, admin: dict[str, str]
) -> None:
    resp = await post(
        client,
        "/api/v1/objects",
        admin,
        {"address_text": "Москва, ул. Старая, д. 5", "customer_phone": "8 916 000 00 01"},
    )
    assert resp.status_code == 201
    assert resp.json()["customer"]["phone"] == CUSTOMER


async def test_only_verified_contractor(client: AsyncClient, admin: dict[str, str]) -> None:
    w = await build_world(client, admin, contractor=False)
    org = await post(
        client,
        "/api/v1/organizations",
        admin,
        {"type": "contractor", "inn": "7728168971", "legal_name": "ООО «Новичок»"},
    )
    resp = await client.put(
        f"/api/v1/objects/{w.object_id}/contractor",
        json={"contractor_org_id": org.json()["id"]},
        headers={**admin, **idem()},
    )
    assert resp.status_code == 409
    resp = await client.put(
        f"/api/v1/objects/{w.object_id}/contractor",
        json={"contractor_org_id": w.org_id},
        headers={**w.customer, **idem()},
    )
    assert resp.status_code == 403


async def test_assign_foreman_by_contractor_admin(
    client: AsyncClient, admin: dict[str, str]
) -> None:
    w = await build_world(client, admin)
    url = f"/api/v1/objects/{w.object_id}/assignments"
    body = {"user_id": w.user_ids["foreman"], "role": "foreman"}
    assert (await post(client, url, w.estimator, body)).status_code == 403
    resp = await post(client, url, w.boss, body)
    assert resp.status_code == 201
    assert (await post(client, url, w.boss, body)).status_code == 409
    # Сметчик — не прораб: назначить его прорабом нельзя.
    resp = await post(client, url, w.boss, {"user_id": w.user_ids["estimator"], "role": "foreman"})
    assert resp.status_code == 422
    # Инженера назначает только админ.
    insp = {"user_id": w.user_ids["inspector"], "role": "inspector"}
    assert (await post(client, url, w.boss, insp)).status_code == 403
    assert (await post(client, url, w.admin, insp)).status_code == 201
    assert (
        await client.get(f"/api/v1/objects/{w.object_id}", headers=w.inspector)
    ).status_code == 200
    listing = (await client.get(url, headers=w.customer)).json()
    assert {a["role"] for a in listing} == {"foreman", "inspector"}
    foreman_assignment = next(a for a in listing if a["role"] == "foreman")
    resp = await client.delete(f"{url}/{foreman_assignment['id']}", headers={**w.boss, **idem()})
    assert resp.status_code == 204


async def test_cancel_object(client: AsyncClient, admin: dict[str, str]) -> None:
    w = await build_world(client, admin)
    assert (await post(client, f"/api/v1/objects/{w.object_id}/cancel", w.boss)).status_code == 403
    resp = await post(client, f"/api/v1/objects/{w.object_id}/cancel", w.customer)
    assert resp.json()["status"] == "cancelled"
    resp = await post(client, f"/api/v1/objects/{w.object_id}/cancel", w.customer)
    assert resp.status_code == 409


async def test_stages_crud(client: AsyncClient, admin: dict[str, str]) -> None:
    w = await build_world(client, admin)
    url = f"/api/v1/objects/{w.object_id}/stages"
    assert (await post(client, url, w.customer, {"title": "Электрика"})).status_code == 403
    first = (await post(client, url, w.estimator, {"title": "Демонтаж"})).json()
    second = (
        await post(client, url, w.estimator, {"title": "Электрика", "is_stop_point": True})
    ).json()
    assert (first["seq"], second["seq"]) == (1, 2)
    bad = await post(
        client,
        url,
        w.estimator,
        {"title": "Стяжка", "planned_start": "2027-02-10", "planned_end": "2027-02-01"},
    )
    assert bad.status_code == 422
    resp = await client.patch(
        f"/api/v1/stages/{first['id']}",
        json={"title": "Демонтаж и вывоз"},
        headers={**w.estimator, **idem()},
    )
    assert resp.json()["title"] == "Демонтаж и вывоз"
    resp = await client.delete(f"/api/v1/stages/{second['id']}", headers={**w.estimator, **idem()})
    assert resp.status_code == 204
    stages = (await client.get(url, headers=w.customer)).json()
    assert [s["title"] for s in stages] == ["Демонтаж и вывоз"]
