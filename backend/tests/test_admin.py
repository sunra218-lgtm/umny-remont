import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.conftest import idem, login


async def test_block_user_revokes_sessions(client: AsyncClient, admin: dict[str, str]) -> None:
    user = await login(client, "+79161234567")
    me = (await client.get("/api/v1/me", headers=user)).json()
    resp = await client.post(f"/api/v1/admin/users/{me['id']}/block", headers={**admin, **idem()})
    assert resp.status_code == 200
    assert resp.json()["blocked_at"] is not None
    assert (await client.get("/api/v1/me", headers=user)).status_code == 401

    from app.core.redis import get_redis

    await get_redis().delete("otp:cool:+79161234567")
    code = (await client.post("/api/v1/auth/otp", json={"phone": "+79161234567"})).json()
    resp = await client.post(
        "/api/v1/auth/otp/verify",
        json={"phone": "+79161234567", "code": code["debug_code"]},
    )
    assert resp.status_code == 403

    resp = await client.post(f"/api/v1/admin/users/{me['id']}/unblock", headers={**admin, **idem()})
    assert resp.json()["blocked_at"] is None


async def test_admin_endpoints_forbidden_for_users(client: AsyncClient) -> None:
    user = await login(client, "+79161234567")
    assert (await client.get("/api/v1/admin/users", headers=user)).status_code == 403
    assert (await client.get("/api/v1/admin/audit-logs", headers=user)).status_code == 403


async def test_users_search(client: AsyncClient, admin: dict[str, str]) -> None:
    await login(client, "+79161234567")
    page = (await client.get("/api/v1/admin/users?q=916123", headers=admin)).json()
    assert [u["phone"] for u in page["items"]] == ["+79161234567"]


async def test_audit_log_records_actions(client: AsyncClient, admin: dict[str, str]) -> None:
    await client.patch("/api/v1/me", json={"full_name": "Админ"}, headers={**admin, **idem()})
    logs = (await client.get("/api/v1/admin/audit-logs?limit=200", headers=admin)).json()
    actions = [entry["action"] for entry in logs["items"]]
    for expected in [
        "user.registered",
        "consent.granted.pd_processing",
        "org_member.added",
        "auth.login",
        "user.updated",
    ]:
        assert expected in actions
    updated = next(e for e in logs["items"] if e["action"] == "user.updated")
    assert updated["new_val"] == {"full_name": "Админ"}
    assert updated["actor_id"] is not None


async def test_audit_log_is_append_only(engine, admin: dict[str, str]) -> None:  # type: ignore[no-untyped-def]
    async with engine.begin() as conn:
        count = (await conn.execute(text("SELECT count(*) FROM audit_logs"))).scalar_one()
        assert count > 0
    for sql in ["UPDATE audit_logs SET action = 'x'", "DELETE FROM audit_logs"]:
        with pytest.raises(DBAPIError, match="append-only"):
            async with engine.begin() as conn:
                await conn.execute(text(sql))
