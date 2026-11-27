"""Общий сценарий для тестов спринта 2: админ, проверенный подрядчик с командой,
инженер платформы и заказчик с объектом."""

from dataclasses import dataclass

from httpx import AsyncClient

from tests.conftest import idem, login

CUSTOMER = "+79160000001"
BOSS = "+79160000002"
ESTIMATOR = "+79160000003"
FOREMAN = "+79160000004"
INSPECTOR = "+79160000005"
STRANGER = "+79160000009"


@dataclass
class World:
    admin: dict[str, str]
    customer: dict[str, str]
    boss: dict[str, str]
    estimator: dict[str, str]
    foreman: dict[str, str]
    inspector: dict[str, str]
    stranger: dict[str, str]
    org_id: str
    platform_org_id: str
    object_id: str
    user_ids: dict[str, str]


async def post(client: AsyncClient, url: str, headers: dict[str, str], json=None):  # type: ignore[no-untyped-def]
    return await client.post(url, json=json, headers={**headers, **idem()})


async def build_world(
    client: AsyncClient, admin: dict[str, str], *, contractor: bool = True
) -> World:
    org = await post(
        client,
        "/api/v1/organizations",
        admin,
        {"type": "contractor", "inn": "7707083893", "legal_name": "ООО «Ремонт»"},
    )
    org_id = org.json()["id"]
    await post(client, f"/api/v1/organizations/{org_id}/verify", admin)
    user_ids = {}
    for phone, role in [(BOSS, "contractor_admin"), (ESTIMATOR, "estimator"), (FOREMAN, "foreman")]:
        r = await post(
            client, f"/api/v1/organizations/{org_id}/members", admin, {"phone": phone, "role": role}
        )
        user_ids[role] = r.json()["user_id"]
    me = (await client.get("/api/v1/me", headers=admin)).json()
    platform_org_id = me["memberships"][0]["org_id"]
    r = await post(
        client,
        f"/api/v1/organizations/{platform_org_id}/members",
        admin,
        {"phone": INSPECTOR, "role": "inspector"},
    )
    user_ids["inspector"] = r.json()["user_id"]

    customer = await login(client, CUSTOMER)
    obj = await post(
        client,
        "/api/v1/objects",
        customer,
        {"address_text": "Москва, ул. Новая, д. 1, кв. 10", "area_m2": "54.3"},
    )
    assert obj.status_code == 201, obj.text
    object_id = obj.json()["id"]
    if contractor:
        r = await client.put(
            f"/api/v1/objects/{object_id}/contractor",
            json={"contractor_org_id": org_id},
            headers={**admin, **idem()},
        )
        assert r.status_code == 200, r.text
    return World(
        admin=admin,
        customer=customer,
        boss=await login(client, BOSS),
        estimator=await login(client, ESTIMATOR),
        foreman=await login(client, FOREMAN),
        inspector=await login(client, INSPECTOR),
        stranger=await login(client, STRANGER),
        org_id=org_id,
        platform_org_id=platform_org_id,
        object_id=object_id,
        user_ids=user_ids,
    )


async def fund_stage(  # type: ignore[no-untyped-def]
    client: AsyncClient,
    engine,
    w: World,
    stage_id: str,
    *,
    work_kop: int = 3_000_000,
    material_kop: int = 1_000_000,
) -> dict:  # type: ignore[type-arg]
    """Смета утверждена (статус и бюджет ставим напрямую), заказчик резервирует и «оплачивает»."""
    from sqlalchemy import text

    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE objects SET status = 'contracting' WHERE id = :id AND status = 'draft'"),
            {"id": w.object_id},
        )
        await conn.execute(
            text("UPDATE stages SET work_budget_kop = :w, material_budget_kop = :m WHERE id = :id"),
            {"w": work_kop, "m": material_kop, "id": stage_id},
        )
    hold = await post(client, f"/api/v1/stages/{stage_id}/payments/hold", w.customer)
    assert hold.status_code == 201, hold.text
    paid = await post(
        client, f"/api/v1/fake-bank/payments/{hold.json()['id']}/pay", w.customer, {"success": True}
    )
    assert paid.json()["status"] == "held", paid.text
    return paid.json()
