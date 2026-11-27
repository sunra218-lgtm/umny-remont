"""Камеры на объекте: подключение, включение заказчиком, телефон-камера, IP-камера, хранение."""

from datetime import timedelta

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select, text

from app.core.db import get_session
from app.core.security import utcnow
from app.models import AuditLog, CameraFrame, Notification
from app.modules.cameras import service
from tests.conftest import idem
from tests.scenario import build_world, post
from tests.test_field import media_dir, photo  # noqa: F401
from tests.test_techcards_daily import _seed


@pytest.fixture
def daytime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "in_hours", lambda cam, now: True)


@pytest.fixture
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    def resolve(host: str) -> list[str]:
        return ["10.0.0.5"] if host.startswith("home") else ["93.184.216.34"]

    monkeypatch.setattr(service, "_resolve", resolve)


async def _activate(engine, object_id: str) -> None:  # type: ignore[no-untyped-def]
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE objects SET status = 'active' WHERE id = :id"), {"id": object_id}
        )


def _frame(token: str, seed: int):  # type: ignore[no-untyped-def]
    return {
        "files": {"photo": ("f.jpg", photo(seed), "image/jpeg")},
        "headers": {"X-Camera-Token": token, **idem()},
    }


async def test_phone_camera(  # type: ignore[no-untyped-def]
    client: AsyncClient,
    admin: dict[str, str],
    engine,
    media_dir,  # noqa: F811
    daytime,
) -> None:
    await _seed()
    w = await build_world(client, admin)
    await _activate(engine, w.object_id)
    url = f"/api/v1/objects/{w.object_id}/cameras"
    assert (
        await post(client, url, w.stranger, {"title": "Кухня", "kind": "phone"})
    ).status_code == 404
    r = await post(client, url, w.boss, {"title": "Кухня", "kind": "phone", "interval_min": 5})
    assert r.status_code == 201, r.text
    cam = r.json()
    assert cam["state"] == "off" and not cam["enabled"] and not cam["can_toggle"]
    token = cam["phone_link"].split("#t=")[1]
    # Заказчику пришло уведомление, ссылка с ключом — только у того, кто подключил.
    async with engine.begin() as conn:
        to = (
            (
                await conn.execute(
                    select(Notification.user_id).where(Notification.template == "camera_added")
                )
            )
            .scalars()
            .all()
        )
    me = (await client.get("/api/v1/me", headers=w.customer)).json()
    assert [str(u) for u in to] == [me["id"]]
    listed = (await client.get(url, headers=w.customer)).json()
    assert listed["items"][0]["phone_link"] is None and listed["items"][0]["can_toggle"]
    assert "России" in listed["consent_text"]

    # Пока заказчик не включил — кадры не принимаются.
    off = await client.post("/api/v1/camera/frames", **_frame(token, 1))
    assert off.status_code == 409 and off.json()["type"] == "/problems/camera-off"
    bad = await client.post("/api/v1/camera/frames", **_frame("nope", 1))
    assert bad.status_code == 401
    st = (await client.get("/api/v1/camera/status", headers={"X-Camera-Token": token})).json()
    assert st["state"] == "off" and "выключил" in st["message"]

    cam_url = f"/api/v1/cameras/{cam['id']}"
    assert (await post(client, f"{cam_url}/enable", w.boss, {"consent": True})).status_code == 403
    assert (
        await post(client, f"{cam_url}/enable", w.customer, {"consent": False})
    ).status_code == 422
    on = (await post(client, f"{cam_url}/enable", w.customer, {"consent": True})).json()
    assert on["enabled"] and on["consent_at"]

    ok = await client.post("/api/v1/camera/frames", **_frame(token, 1))
    assert ok.status_code == 200, ok.text
    assert ok.json()["frames_today"] == 1 and ok.json()["next_in_s"] > 200
    early = await client.post("/api/v1/camera/frames", **_frame(token, 2))
    assert early.status_code == 429
    junk = {
        "files": {"photo": ("f.jpg", b"xx", "image/jpeg")},
        "headers": {"X-Camera-Token": token, **idem()},
    }
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE cameras SET last_frame_at = now() - interval '6 minutes'"))
    assert (await client.post("/api/v1/camera/frames", **junk)).status_code == 422
    assert (await client.post("/api/v1/camera/frames", **_frame(token, 3))).status_code == 200

    # Таймлапс: кадры видят участники, кадр размечен по этапу из графика.
    fr = (await client.get(f"{cam_url}/frames", headers=w.customer)).json()
    assert len(fr["frames"]) == 2 and fr["days"][0]["frames"] == 2 and not fr["can_label"]
    assert fr["frames"][1]["changed"]
    img = await client.get(fr["frames"][0]["url"])
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    assert (await client.get(f"{cam_url}/frames", headers=w.stranger)).status_code == 404

    # Инженер размечает кадр вручную.
    fid = fr["frames"][0]["id"]
    sid = (
        await post(
            client,
            f"/api/v1/objects/{w.object_id}/stages",
            w.estimator,
            {"title": "Электрика", "stage_type": "electric"},
        )
    ).json()["id"]
    lbl = {"stage_id": sid, "hidden_work": True, "note": "Видно трассы кабеля"}
    patch = lambda h: client.patch(  # noqa: E731
        f"/api/v1/camera-frames/{fid}", json=lbl, headers={**h, **idem()}
    )
    assert (await patch(w.boss)).status_code == 403
    # Чужой объект инженер не видит: размечает назначенный инженер или администратор.
    assert (await patch(w.inspector)).status_code == 404
    done = (await patch(admin)).json()
    assert done["labeled_by"] == "engineer" and done["hidden_work"]
    assert done["stage_title"] == "Электрика"
    hidden = (await client.get(f"{cam_url}/frames?hidden_only=true", headers=w.customer)).json()
    assert [f["id"] for f in hidden["frames"]] == [fid]

    # Часы съёмки меняет только заказчик; выключение — сразу.
    hours = {"hours": {"hour_from": 10, "hour_to": 18}}
    assert (
        await client.patch(cam_url, json=hours, headers={**w.boss, **idem()})
    ).status_code == 403
    assert (
        await client.patch(
            cam_url,
            json={"hours": {"hour_from": 18, "hour_to": 10}},
            headers={**w.customer, **idem()},
        )
    ).status_code == 422
    h = (await client.patch(cam_url, json=hours, headers={**w.customer, **idem()})).json()
    assert (h["hour_from"], h["hour_to"]) == (10, 18)
    off = (await post(client, f"{cam_url}/disable", w.customer)).json()
    assert not off["enabled"] and off["state"] == "off"
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE cameras SET last_frame_at = now() - interval '1 hour'"))
    assert (await client.post("/api/v1/camera/frames", **_frame(token, 4))).status_code == 409

    # Новая ссылка отключает старую; после удаления камеры ссылка не работает.
    fresh = (await post(client, f"{cam_url}/phone-link", w.boss)).json()["phone_link"]
    st = await client.get("/api/v1/camera/status", headers={"X-Camera-Token": token})
    assert st.status_code == 401
    new_token = fresh.split("#t=")[1]
    assert (
        await client.get("/api/v1/camera/status", headers={"X-Camera-Token": new_token})
    ).status_code == 200
    assert (await client.delete(cam_url, headers={**w.boss, **idem()})).status_code == 204
    assert (
        await client.get("/api/v1/camera/status", headers={"X-Camera-Token": new_token})
    ).status_code == 401
    async with engine.begin() as conn:
        actions = set(
            (await conn.execute(select(AuditLog.action).where(AuditLog.entity_type == "camera")))
            .scalars()
            .all()
        )
    assert {"camera.enabled", "camera.disabled", "camera.deleted"} <= actions


async def test_off_hours_and_object_status(  # type: ignore[no-untyped-def]
    client: AsyncClient,
    admin: dict[str, str],
    engine,
    media_dir,  # noqa: F811
) -> None:
    w = await build_world(client, admin)
    url = f"/api/v1/objects/{w.object_id}/cameras"
    cam = (
        await post(
            client, url, w.customer, {"title": "Зал", "kind": "phone", "hour_from": 0, "hour_to": 1}
        )
    ).json()
    token = cam["phone_link"].split("#t=")[1]
    await post(client, f"/api/v1/cameras/{cam['id']}/enable", w.customer, {"consent": True})
    r = await client.post("/api/v1/camera/frames", **_frame(token, 1))
    assert r.status_code == 409 and r.json()["type"] == "/problems/object-not-in-work"
    await _activate(engine, w.object_id)
    # Сравниваем с правилом напрямую, чтобы тест не зависел от времени запуска.
    from app.models import Camera

    async for db in get_session():
        c = await db.get(Camera, cam["id"])
        assert c is not None
        expected = "sleeping" if not service.in_hours(c, utcnow()) else None
    if expected:
        r = await client.post("/api/v1/camera/frames", **_frame(token, 1))
        assert r.json()["type"] == "/problems/off-hours"
    for _ in range(3):
        await post(client, url, w.customer, {"title": "Ещё", "kind": "phone"})
    assert (
        await post(client, url, w.customer, {"title": "Пятая", "kind": "phone"})
    ).status_code == 409


async def test_ip_camera(  # type: ignore[no-untyped-def]
    client: AsyncClient,
    admin: dict[str, str],
    engine,
    media_dir,  # noqa: F811
    daytime,
    public_dns,
) -> None:
    w = await build_world(client, admin)
    await _activate(engine, w.object_id)
    url = f"/api/v1/objects/{w.object_id}/cameras"
    body = {"title": "Ванная", "kind": "ip"}
    assert (await post(client, url, w.boss, body)).status_code == 422
    home = await post(client, url, w.boss, {**body, "snapshot_url": "http://home.lan/snap.jpg"})
    assert home.status_code == 422 and "интернет" in home.json()["title"]
    ftp = await post(client, url, w.boss, {**body, "snapshot_url": "ftp://cam.example.ru/x"})
    assert ftp.status_code == 422
    cam = (
        await post(
            client,
            url,
            w.boss,
            {**body, "snapshot_url": "http://admin:secret@cam.example.ru:8080/snap.jpg"},
        )
    ).json()
    # Пароль от камеры никому не показываем.
    assert cam["snapshot_url"] == "http://cam.example.ru:8080/snap.jpg"
    assert cam["phone_link"] is None

    seen: list[httpx.Request] = []
    pics = iter([photo(11), photo(11), photo(12)])

    def camera(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if "authorization" not in request.headers:
            return httpx.Response(401)
        return httpx.Response(200, content=next(pics), headers={"content-type": "image/jpeg"})

    transport = httpx.MockTransport(camera)
    now = utcnow()
    async for db in get_session():
        # Выключена заказчиком — сервер к камере не ходит.
        assert await service.capture_ip_cameras(db, now, transport) == 0
    assert seen == []
    await post(client, f"/api/v1/cameras/{cam['id']}/enable", w.customer, {"consent": True})
    async for db in get_session():
        assert await service.capture_ip_cameras(db, now, transport) == 1
        assert await service.capture_ip_cameras(db, now + timedelta(minutes=5), transport) == 0
        assert await service.capture_ip_cameras(db, now + timedelta(minutes=15), transport) == 1
        assert await service.capture_ip_cameras(db, now + timedelta(minutes=30), transport) == 1
    assert seen[0].url.host == "cam.example.ru" and "secret" not in str(seen[0].url)
    async with engine.begin() as conn:
        changed = (
            (await conn.execute(select(CameraFrame.changed).order_by(CameraFrame.taken_at)))
            .scalars()
            .all()
        )
    assert changed == [True, False, True]  # одинаковый кадр — «ничего не происходило»

    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async for db in get_session():
        later = now + timedelta(minutes=45)
        assert await service.capture_ip_cameras(db, later, httpx.MockTransport(broken)) == 0
    out = (await client.get(url, headers=w.customer)).json()["items"][0]
    assert out["last_error"] == "Камера ответила ошибкой 503"

    # Кадры старше срока хранения удаляются вместе с файлами.
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE camera_frames SET taken_at = now() - interval '100 days' WHERE NOT changed"
            )
        )
    async for db in get_session():
        assert await service.purge_old_frames(db) == 1
    async with engine.begin() as conn:
        left = (await conn.execute(text("SELECT count(*) FROM camera_frames"))).scalar()
    assert left == 2
