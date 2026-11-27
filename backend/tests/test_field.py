"""Спринт 3: вход бригады по ссылке, подпись устройства, фотоотчёт и антифрод."""

import base64
import hashlib
import io
import random
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from httpx import AsyncClient
from PIL import Image

from app.core.storage import LocalStorage, set_storage
from tests.conftest import idem
from tests.scenario import World, build_world, post


@pytest.fixture(autouse=True)
def media_dir(tmp_path: Path) -> None:
    set_storage(LocalStorage(str(tmp_path)))


class Device:
    """Телефон прораба: ключ P-256, подпись в формате WebCrypto (r||s)."""

    def __init__(self) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        spki = self.key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        self.public_key = base64.b64encode(spki).decode()

    def sign(self, message: str) -> str:
        der = self.key.sign(message.encode(), ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        return base64.b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big")).decode()


def photo(seed: int, quality: int = 85) -> bytes:
    rnd = random.Random(seed)  # noqa: S311
    img = Image.new("RGB", (64, 48))
    img.putdata(
        [(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)) for _ in range(64 * 48)]
    )
    img = img.resize((640, 480), Image.Resampling.BILINEAR)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


async def foreman_on_phone(client: AsyncClient, w: World) -> tuple[dict[str, str], Device, str]:
    link = await post(
        client,
        "/api/v1/auth/links",
        w.boss,
        {"user_id": w.user_ids["foreman"], "object_id": w.object_id},
    )
    assert link.status_code == 201, link.text
    token = link.json()["debug_url"].rsplit("/", 1)[-1]
    device = Device()
    resp = await client.post(
        f"/api/v1/auth/links/{token}/exchange", json={"public_key": device.public_key}
    )
    assert resp.status_code == 200, resp.text
    # Ссылка одноразовая.
    again = await client.post(
        f"/api/v1/auth/links/{token}/exchange", json={"public_key": device.public_key}
    )
    assert again.status_code == 410
    return (
        {"Authorization": f"Bearer {resp.json()['access_token']}"},
        device,
        resp.json()["refresh_token"],
    )


async def stage(client: AsyncClient, w: World) -> str:
    r = await post(
        client, f"/api/v1/objects/{w.object_id}/stages", w.estimator, {"title": "Стяжка"}
    )
    return r.json()["id"]


async def upload(
    client: AsyncClient,
    lite: dict[str, str],
    device: Device,
    stage_id: str,
    data: bytes,
    *,
    challenge: dict | None = None,  # type: ignore[type-arg]
    extra: dict[str, str] | None = None,
    bad_sig: bool = False,
):  # type: ignore[no-untyped-def]
    code = challenge["code"] if challenge else ""
    sig = device.sign(f"{hashlib.sha256(data).hexdigest()}:{code}")
    if bad_sig:
        sig = Device().sign("x")
    form = {"signature": sig, **(extra or {})}
    if challenge:
        form["challenge_id"] = challenge["challenge_id"]
    return await client.post(
        f"/api/v1/stages/{stage_id}/media",
        data=form,
        files={"file": ("p.jpg", data, "image/jpeg")},
        headers={**lite, **idem()},
    )


async def test_link_only_for_own_foreman(client: AsyncClient, admin: dict[str, str]) -> None:
    w = await build_world(client, admin)
    body = {"user_id": w.user_ids["estimator"], "object_id": w.object_id}
    assert (await post(client, "/api/v1/auth/links", w.boss, body)).status_code == 422
    body["user_id"] = w.user_ids["foreman"]
    assert (await post(client, "/api/v1/auth/links", w.customer, body)).status_code == 403
    bad = await client.post(
        "/api/v1/auth/links/nope/exchange", json={"public_key": Device().public_key}
    )
    assert bad.status_code == 410


async def test_lite_refresh_needs_device_signature(
    client: AsyncClient, admin: dict[str, str]
) -> None:
    w = await build_world(client, admin)
    _, device, refresh = await foreman_on_phone(client, w)
    client.cookies.clear()
    url = "/api/v1/auth/refresh"
    unsigned = await client.post(url, json={"refresh_token": refresh})
    assert unsigned.status_code == 401
    assert unsigned.json()["type"].endswith("device-signature-required")
    ts = str(int(time.time() * 1000))
    forged = await client.post(
        url,
        json={"refresh_token": refresh},
        headers={"X-Device-Timestamp": ts, "X-Device-Signature": Device().sign(f"refresh:{ts}")},
    )
    assert forged.status_code == 401
    old = str(int(time.time() * 1000) - 3_600_000)
    stale = await client.post(
        url,
        json={"refresh_token": refresh},
        headers={"X-Device-Timestamp": old, "X-Device-Signature": device.sign(f"refresh:{old}")},
    )
    assert stale.status_code == 401
    ok = await client.post(
        url,
        json={"refresh_token": refresh},
        headers={"X-Device-Timestamp": ts, "X-Device-Signature": device.sign(f"refresh:{ts}")},
    )
    assert ok.status_code == 200, ok.text


async def test_photo_report_flow(client: AsyncClient, admin: dict[str, str]) -> None:
    w = await build_world(client, admin)
    await client.patch(
        f"/api/v1/objects/{w.object_id}",
        json={"geo_lat": "55.751244", "geo_lon": "37.618423"},
        headers={**w.customer, **idem()},
    )
    lite, device, _ = await foreman_on_phone(client, w)
    stage_id = await stage(client, w)

    # Код съёмки выдаётся только в приложении бригады.
    web = await post(client, f"/api/v1/stages/{stage_id}/capture-challenges", w.foreman)
    assert web.status_code == 403
    ch = (await post(client, f"/api/v1/stages/{stage_id}/capture-challenges", lite)).json()
    assert len(ch["code"]) == 6

    near = {"geo_lat": "55.7513", "geo_lon": "37.6185", "geo_accuracy_m": "20"}
    first = await upload(client, lite, device, stage_id, photo(1), challenge=ch, extra=near)
    assert first.status_code == 201, first.text
    assert first.json()["fraud_flags"] == {}

    # Тот же код второй раз, чужая точка, повтор кадра, кадр без кода.
    far = {"geo_lat": "55.80", "geo_lon": "37.70", "geo_accuracy_m": "15"}
    second = await upload(client, lite, device, stage_id, photo(1, 60), challenge=ch, extra=far)
    flags = second.json()["fraud_flags"]
    assert {"challenge_reused", "geo_mismatch", "duplicate_similar"} <= set(flags)
    third = await upload(client, lite, device, stage_id, photo(1))
    assert {"no_challenge", "no_geo", "duplicate_exact"} <= set(third.json()["fraud_flags"])

    # Подпись чужим ключом и не фото отклоняются.
    assert (await upload(client, lite, device, stage_id, photo(2), bad_sig=True)).status_code == 400
    assert (await upload(client, lite, device, stage_id, b"not an image")).status_code == 422

    feed = (await client.get(f"/api/v1/objects/{w.object_id}/feed", headers=w.boss)).json()
    assert len(feed["items"]) == 3
    assert feed["items"][0]["fraud_flags"]
    customer_feed = (
        await client.get(f"/api/v1/objects/{w.object_id}/feed", headers=w.customer)
    ).json()
    assert all(i["fraud_flags"] is None for i in customer_feed["items"])
    stranger = await client.get(f"/api/v1/objects/{w.object_id}/feed", headers=w.stranger)
    assert stranger.status_code in (403, 404)

    url = customer_feed["items"][-1]["url"]
    img = await client.get(url)
    assert img.status_code == 200
    assert img.headers["content-type"] == "image/jpeg"
    assert img.content == photo(1)
    assert (await client.get(url.replace("sig=", "sig=x"))).status_code == 403

    page = await client.get(f"/api/v1/objects/{w.object_id}/feed?limit=2", headers=w.customer)
    nxt = page.json()["next_cursor"]
    rest = await client.get(
        f"/api/v1/objects/{w.object_id}/feed?limit=2&cursor={nxt}", headers=w.customer
    )
    assert len(rest.json()["items"]) == 1
    stats = await client.get(f"/api/v1/objects/{w.object_id}/feed/stats", headers=w.customer)
    assert stats.json() == {"days_with_reports": 1, "photos": 3}


async def test_expired_challenge_offline(
    client: AsyncClient,
    admin: dict[str, str],
    engine,  # type: ignore[no-untyped-def]
) -> None:
    from datetime import timedelta

    from sqlalchemy import update

    from app.models import CaptureChallenge

    w = await build_world(client, admin)
    lite, device, _ = await foreman_on_phone(client, w)
    stage_id = await stage(client, w)
    chs = [
        (await post(client, f"/api/v1/stages/{stage_id}/capture-challenges", lite)).json()
        for _ in range(2)
    ]
    async with engine.begin() as conn:
        await conn.execute(
            update(CaptureChallenge).values(
                expires_at=CaptureChallenge.expires_at - timedelta(hours=2)
            )
        )
    late = await upload(
        client, lite, device, stage_id, photo(5), challenge=chs[0], extra={"elapsed_ms": "30000"}
    )
    assert "delayed_upload" in late.json()["fraud_flags"]
    expired = await upload(client, lite, device, stage_id, photo(6), challenge=chs[1])
    assert "challenge_expired" in expired.json()["fraud_flags"]


async def test_org_devices_and_revoke(client: AsyncClient, admin: dict[str, str]) -> None:
    w = await build_world(client, admin)
    lite, _, _ = await foreman_on_phone(client, w)
    url = f"/api/v1/organizations/{w.org_id}/devices"
    assert (await client.get(url, headers=w.foreman)).status_code == 403
    devices = (await client.get(url, headers=w.boss)).json()
    phone = next(d for d in devices if d["kind"] == "lite")
    r = await client.delete(f"/api/v1/auth/devices/{phone['id']}", headers={**w.boss, **idem()})
    assert r.status_code == 204, r.text
    assert (await client.get("/api/v1/me", headers=lite)).status_code == 401
