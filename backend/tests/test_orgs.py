from httpx import AsyncClient

from tests.conftest import idem, login

CONTRACTOR = {
    "type": "contractor",
    "inn": "7707083893",
    "legal_name": "ООО «Ремонт-Сервис»",
    "ogrn": "1027700132195",
    "kpp": "773601001",
    "bank_bik": "044525225",
    "bank_account": "40702810938000000001",
}


async def _create(client: AsyncClient, admin: dict[str, str], **override):  # type: ignore[no-untyped-def]
    return await client.post(
        "/api/v1/organizations", json={**CONTRACTOR, **override}, headers={**admin, **idem()}
    )


async def test_admin_creates_org(client: AsyncClient, admin: dict[str, str]) -> None:
    resp = await _create(client, admin)
    assert resp.status_code == 201, resp.text
    assert resp.json()["inn"] == "7707083893"
    assert resp.json()["verified_at"] is None


async def test_invalid_requisites_rejected(client: AsyncClient, admin: dict[str, str]) -> None:
    assert (await _create(client, admin, inn="7707083894")).status_code == 422
    assert (await _create(client, admin, ogrn="1027700132196")).status_code == 422
    assert (await _create(client, admin, bank_account="40702810938000000002")).status_code == 422
    assert (await _create(client, admin, type="platform")).status_code == 422


async def test_duplicate_inn_conflict(client: AsyncClient, admin: dict[str, str]) -> None:
    assert (await _create(client, admin)).status_code == 201
    resp = await _create(client, admin)
    assert resp.status_code == 409
    assert resp.json()["type"] == "/problems/inn-taken"


async def test_regular_user_cannot_create_org(client: AsyncClient) -> None:
    user = await login(client, "+79160000002")
    assert (await _create(client, user)).status_code == 403


async def test_member_flow_and_visibility(client: AsyncClient, admin: dict[str, str]) -> None:
    org_id = (await _create(client, admin)).json()["id"]
    resp = await client.post(
        f"/api/v1/organizations/{org_id}/members",
        json={"phone": "8 916 111-22-33", "role": "contractor_admin", "full_name": "Иван Петров"},
        headers={**admin, **idem()},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["phone"] == "+79161112233"

    boss = await login(client, "+79161112233")
    me = (await client.get("/api/v1/me", headers=boss)).json()
    assert me["full_name"] == "Иван Петров"
    assert me["memberships"][0]["role"] == "contractor_admin"

    # Руководитель подрядчика видит только свою организацию и может добавить прораба.
    orgs = (await client.get("/api/v1/organizations", headers=boss)).json()
    assert [o["id"] for o in orgs["items"]] == [org_id]
    resp = await client.post(
        f"/api/v1/organizations/{org_id}/members",
        json={"phone": "+79162223344", "role": "foreman"},
        headers={**boss, **idem()},
    )
    assert resp.status_code == 201
    # Роль поставщика в организацию подрядчика добавить нельзя.
    resp = await client.post(
        f"/api/v1/organizations/{org_id}/members",
        json={"phone": "+79162223355", "role": "supplier_manager"},
        headers={**boss, **idem()},
    )
    assert resp.status_code == 422
    assert resp.json()["type"] == "/problems/role-not-allowed"

    foreman = await login(client, "+79162223344")
    resp = await client.post(
        f"/api/v1/organizations/{org_id}/members",
        json={"phone": "+79162223366", "role": "foreman"},
        headers={**foreman, **idem()},
    )
    assert resp.status_code == 403

    members = (await client.get(f"/api/v1/organizations/{org_id}/members", headers=foreman)).json()
    assert {m["role"] for m in members} == {"contractor_admin", "foreman"}

    stranger = await login(client, "+79169998877")
    assert (
        await client.get(f"/api/v1/organizations/{org_id}", headers=stranger)
    ).status_code == 404


async def test_cannot_remove_last_manager(client: AsyncClient, admin: dict[str, str]) -> None:
    org_id = (await _create(client, admin)).json()["id"]
    member = (
        await client.post(
            f"/api/v1/organizations/{org_id}/members",
            json={"phone": "+79161112233", "role": "contractor_admin"},
            headers={**admin, **idem()},
        )
    ).json()
    resp = await client.delete(
        f"/api/v1/organizations/{org_id}/members/{member['id']}", headers={**admin, **idem()}
    )
    assert resp.status_code == 409


async def test_update_and_verify(client: AsyncClient, admin: dict[str, str]) -> None:
    org_id = (await _create(client, admin)).json()["id"]
    resp = await client.patch(
        f"/api/v1/organizations/{org_id}",
        json={"legal_name": "ООО «Новый Ремонт»"},
        headers={**admin, **idem()},
    )
    assert resp.json()["legal_name"] == "ООО «Новый Ремонт»"
    resp = await client.post(f"/api/v1/organizations/{org_id}/verify", headers={**admin, **idem()})
    assert resp.json()["verified_at"] is not None


async def test_list_pagination(client: AsyncClient, admin: dict[str, str]) -> None:
    for inn in ["7707083893", "7728168971", "7736050003"]:
        assert (await _create(client, admin, inn=inn, ogrn=None)).status_code == 201
    page = (await client.get("/api/v1/organizations?type=contractor&limit=2", headers=admin)).json()
    assert len(page["items"]) == 2 and page["next_cursor"]
    rest = (
        await client.get(
            f"/api/v1/organizations?type=contractor&limit=2&cursor={page['next_cursor']}",
            headers=admin,
        )
    ).json()
    assert len(rest["items"]) == 1 and rest["next_cursor"] is None
