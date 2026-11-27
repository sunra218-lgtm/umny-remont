from httpx import AsyncClient

from tests.conftest import login


async def test_mutation_requires_key(client: AsyncClient) -> None:
    user = await login(client, "+79161234567")
    resp = await client.patch("/api/v1/me", json={"full_name": "А"}, headers=user)
    assert resp.status_code == 400
    assert resp.json()["type"] == "/problems/idempotency-key-required"


async def test_replay_returns_same_response_without_repeating(
    client: AsyncClient, admin: dict[str, str]
) -> None:
    headers = {**admin, "Idempotency-Key": "test-key-0001"}
    body = {"type": "supplier", "inn": "7728168971", "legal_name": "ООО «Стройматериалы»"}
    first = await client.post("/api/v1/organizations", json=body, headers=headers)
    second = await client.post("/api/v1/organizations", json=body, headers=headers)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert second.headers.get("idempotent-replay") == "true"
    orgs = (await client.get("/api/v1/organizations?type=supplier", headers=admin)).json()
    assert len(orgs["items"]) == 1


async def test_same_key_different_body_rejected(client: AsyncClient, admin: dict[str, str]) -> None:
    headers = {**admin, "Idempotency-Key": "test-key-0002"}
    await client.patch("/api/v1/me", json={"full_name": "А"}, headers=headers)
    resp = await client.patch("/api/v1/me", json={"full_name": "Б"}, headers=headers)
    assert resp.status_code == 422
    assert resp.json()["type"] == "/problems/idempotency-key-reused"
