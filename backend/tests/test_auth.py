from httpx import AsyncClient

from tests.conftest import ADMIN_PHONE, login

PHONE = "+79161234567"


async def _send(client: AsyncClient, phone: str = PHONE):  # type: ignore[no-untyped-def]
    return await client.post("/api/v1/auth/otp", json={"phone": phone})


async def test_health(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_phone_is_normalized(client: AsyncClient) -> None:
    resp = await _send(client, "8 (916) 123-45-67")
    assert resp.status_code == 202
    assert resp.json()["phone"] == PHONE
    assert len(resp.json()["debug_code"]) == 6


async def test_invalid_phone_is_problem_json(client: AsyncClient) -> None:
    resp = await _send(client, "12345")
    assert resp.status_code == 422
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.json()["type"] == "/problems/validation-error"


async def test_full_login_flow_creates_user_and_consent(client: AsyncClient) -> None:
    headers = await login(client, PHONE, marketing=True)
    me = await client.get("/api/v1/me", headers=headers)
    assert me.status_code == 200
    body = me.json()
    assert body["phone"] == PHONE
    assert body["is_platform_admin"] is False
    assert {c["type"] for c in body["consents"]} == {"pd_processing", "marketing"}


async def test_consent_required_for_new_user_and_code_not_burned(client: AsyncClient) -> None:
    code = (await _send(client)).json()["debug_code"]
    resp = await client.post("/api/v1/auth/otp/verify", json={"phone": PHONE, "code": code})
    assert resp.status_code == 422
    assert resp.json()["type"] == "/problems/consent-required"
    # Тот же код всё ещё работает после того, как пользователь отметил согласие.
    resp = await client.post(
        "/api/v1/auth/otp/verify",
        json={"phone": PHONE, "code": code, "accept_pd_processing": True},
    )
    assert resp.status_code == 200
    assert resp.json()["is_new_user"] is True


async def test_returning_user_does_not_need_consent_again(client: AsyncClient) -> None:
    await login(client, PHONE)
    from app.core.redis import get_redis

    await get_redis().delete(f"otp:cool:{PHONE}")
    code = (await _send(client)).json()["debug_code"]
    resp = await client.post("/api/v1/auth/otp/verify", json={"phone": PHONE, "code": code})
    assert resp.status_code == 200
    assert resp.json()["is_new_user"] is False


async def test_wrong_code_then_attempts_exceeded(client: AsyncClient) -> None:
    code = (await _send(client)).json()["debug_code"]
    wrong = "000000" if code != "000000" else "111111"
    payload = {"phone": PHONE, "code": wrong, "accept_pd_processing": True}
    for left in range(4, -1, -1):
        resp = await client.post("/api/v1/auth/otp/verify", json=payload)
        assert resp.status_code == 400
        assert resp.json()["attempts_left"] == left
    resp = await client.post("/api/v1/auth/otp/verify", json=payload)
    assert resp.status_code == 429
    # После превышения попыток даже верный код не принимается.
    payload["code"] = code
    resp = await client.post("/api/v1/auth/otp/verify", json=payload)
    assert resp.status_code == 400
    assert resp.json()["type"] == "/problems/otp-expired"


async def test_code_is_single_use(client: AsyncClient) -> None:
    code = (await _send(client)).json()["debug_code"]
    payload = {"phone": PHONE, "code": code, "accept_pd_processing": True}
    assert (await client.post("/api/v1/auth/otp/verify", json=payload)).status_code == 200
    resp = await client.post("/api/v1/auth/otp/verify", json=payload)
    assert resp.status_code == 400


async def test_resend_cooldown(client: AsyncClient) -> None:
    assert (await _send(client)).status_code == 202
    resp = await _send(client)
    assert resp.status_code == 429
    assert resp.json()["type"] == "/problems/otp-cooldown"
    assert int(resp.headers["retry-after"]) > 0


async def test_hourly_limit(client: AsyncClient) -> None:
    from app.core.redis import get_redis

    for _ in range(5):
        await get_redis().delete(f"otp:cool:{PHONE}")
        assert (await _send(client)).status_code == 202
    await get_redis().delete(f"otp:cool:{PHONE}")
    resp = await _send(client)
    assert resp.status_code == 429
    assert resp.json()["type"] == "/problems/otp-hour-limit"


async def test_me_requires_token(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/me")
    assert resp.status_code == 401
    resp = await client.get("/api/v1/me", headers={"Authorization": "Bearer garbage"})
    assert resp.status_code == 401


async def test_refresh_rotates_token_via_cookie(client: AsyncClient) -> None:
    await login(client, PHONE)
    old_cookie = client.cookies.get("ur_refresh")
    assert old_cookie
    resp = await client.post("/api/v1/auth/refresh")
    assert resp.status_code == 200
    new_cookie = client.cookies.get("ur_refresh")
    assert new_cookie and new_cookie != old_cookie
    # Старый refresh-токен больше не действует.
    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": old_cookie})
    assert resp.status_code == 401


async def test_logout_revokes_session(client: AsyncClient) -> None:
    headers = await login(client, PHONE)
    assert (await client.post("/api/v1/auth/logout", headers=headers)).status_code == 204
    resp = await client.get("/api/v1/me", headers=headers)
    assert resp.status_code == 401
    assert resp.json()["type"] == "/problems/session-revoked"


async def test_devices_list_and_revoke_other_device(client: AsyncClient) -> None:
    first = await login(client, PHONE)
    second = await login(client, PHONE)
    devices = (await client.get("/api/v1/auth/devices", headers=second)).json()
    assert len(devices) == 2
    other = next(d for d in devices if not d["is_current"])
    resp = await client.delete(f"/api/v1/auth/devices/{other['id']}", headers=second)
    assert resp.status_code == 204
    assert (await client.get("/api/v1/me", headers=first)).status_code == 401
    assert (await client.get("/api/v1/me", headers=second)).status_code == 200


async def test_cannot_revoke_someone_elses_device(client: AsyncClient) -> None:
    mine = await login(client, PHONE)
    theirs = await login(client, "+79167654321")
    their_device = (await client.get("/api/v1/auth/devices", headers=theirs)).json()[0]
    resp = await client.delete(f"/api/v1/auth/devices/{their_device['id']}", headers=mine)
    assert resp.status_code == 404


async def test_bootstrap_admin_gets_platform_role(client: AsyncClient) -> None:
    headers = await login(client, ADMIN_PHONE)
    me = (await client.get("/api/v1/me", headers=headers)).json()
    assert me["is_platform_admin"] is True
    assert me["memberships"][0]["org_type"] == "platform"
    assert me["memberships"][0]["role"] == "platform_admin"
    # Повторный вход не создаёт дубликатов.
    headers = await login(client, ADMIN_PHONE)
    me = (await client.get("/api/v1/me", headers=headers)).json()
    assert len(me["memberships"]) == 1
