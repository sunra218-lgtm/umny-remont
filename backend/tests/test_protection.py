"""Спринт 7: заголовки безопасности, ограничение частоты запросов, проверки настроек сервера."""

import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from tests.conftest import idem

GOOD_PROD = {
    "env": "prod",
    "otp_debug": False,
    "jwt_secret": "x" * 40,
    "cookie_secure": True,
    "bank_webhook_secret": "y" * 40,
    "public_base_url": "https://umny-remont.ru",
}


async def test_security_headers(client: AsyncClient) -> None:
    r = await client.get("/api/v1/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"
    assert "strict-transport-security" not in r.headers  # только на сервере с HTTPS


async def test_rate_limit(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "rate_limit_per_minute", 5)
    monkeypatch.setattr(s, "rate_limit_writes_per_minute", 2)
    from app.core.redis import get_redis

    await get_redis().flushall()
    # Изменяющие запросы ограничены строже.
    codes = [
        (await client.post("/api/v1/objects", json={}, headers=idem())).status_code
        for _ in range(3)
    ]
    assert codes[:2] == [401, 401] and codes[2] == 429
    reads = [(await client.get("/api/v1/me")).status_code for _ in range(3)]
    assert reads[-1] == 429
    limited = await client.get("/api/v1/me")
    assert limited.json()["type"].endswith("rate-limited")
    assert int(limited.headers["retry-after"]) <= 60
    assert limited.headers["x-content-type-options"] == "nosniff"
    # Проверка работоспособности не ограничивается.
    assert (await client.get("/api/v1/health")).status_code == 200


def test_prod_settings_guard() -> None:
    Settings(**GOOD_PROD)  # type: ignore[arg-type]
    for bad in (
        {"jwt_secret": "short"},
        {"cookie_secure": False},
        {"otp_debug": True},
        {"bank_webhook_secret": "dev-bank-webhook-secret"},
        {"public_base_url": "http://umny-remont.ru"},
    ):
        with pytest.raises(ValidationError):
            Settings(**{**GOOD_PROD, **bad})  # type: ignore[arg-type]


def test_prod_hides_api_docs(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.main import create_app

    monkeypatch.setattr("app.main.get_settings", lambda: Settings(**GOOD_PROD))  # type: ignore[arg-type]
    app = create_app()
    assert app.openapi_url is None and app.docs_url is None


async def test_pilot_kpi(client: AsyncClient, admin: dict[str, str], engine, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import uuid
    from datetime import datetime, timedelta

    from sqlalchemy import text

    from app.modules.admin import kpi
    from app.modules.notifications.service import MSK
    from tests.scenario import build_world, fund_stage, post
    from tests.test_procurement_disputes import _sign

    w = await build_world(client, admin)
    sid = (
        await post(client, f"/api/v1/objects/{w.object_id}/stages", w.estimator, {"title": "Пол"})
    ).json()["id"]
    await fund_stage(client, engine, w, sid)
    await post(client, f"/api/v1/stages/{sid}/start", w.boss)
    await post(client, f"/api/v1/stages/{sid}/submit", w.boss)
    act = (await post(client, f"/api/v1/stages/{sid}/acts", w.boss)).json()
    await _sign(client, w.boss, act["id"])
    await _sign(client, w.customer, act["id"])

    start = datetime.now(MSK).date()
    fake_now = datetime.combine(start + timedelta(days=14), datetime.min.time(), MSK)
    monkeypatch.setattr(kpi, "utcnow", lambda: fake_now)
    work = kpi.working_days(start, fake_now.date())
    weekend = next(
        start + timedelta(days=i) for i in range(7) if (start + timedelta(days=i)).weekday() >= 5
    )
    shots = [work[0], work[0], work[1], work[2], weekend]
    async with engine.begin() as conn:
        for n, d in enumerate(shots):
            await conn.execute(
                text(
                    "INSERT INTO media_reports (id, object_id, author_id, media_type, s3_key,"
                    " sha256, received_server_at, fraud_flags, review_status)"
                    " VALUES (:id, :o, :a, 'photo', 'k', :h, :t, '{}', 'pending')"
                ),
                {
                    "id": uuid.uuid4(),
                    "o": w.object_id,
                    "a": w.user_ids["foreman"],
                    "h": f"{n:064d}",
                    "t": datetime.combine(d, datetime.min.time(), MSK) + timedelta(hours=12),
                },
            )

    assert (await client.get("/api/v1/admin/kpi", headers=w.customer)).status_code == 403
    data = (await client.get("/api/v1/admin/kpi", headers=admin)).json()
    m = {x["key"]: x for x in data["metrics"]}
    assert m["objects"]["value"] == 1 and not m["objects"]["ok"]
    assert m["contractors"]["value"] == 1
    obj = data["objects"][0]
    assert obj["working_days"] == len(work) and obj["days_with_reports"] == 3
    assert m["report_days"]["value"] == round(100 * 3 / len(work), 1)
    assert m["cycles"]["value"] == 1 and not m["cycles"]["ok"]  # банк — имитация
    assert "имитация" in m["cycles"]["note"]
    assert m["baskets"]["value"] == 0


def test_working_days() -> None:
    from datetime import date

    from app.modules.admin.kpi import working_days

    # 23.11.2026 — понедельник, 29.11 — воскресенье.
    assert len(working_days(date(2026, 11, 23), date(2026, 11, 29))) == 5
    assert working_days(date(2026, 11, 28), date(2026, 11, 29)) == []
