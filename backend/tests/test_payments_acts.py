"""Спринт 5: резерв этапа через банк (имитация), акт с подписями по СМС, выплата."""

import json
import uuid

from httpx import AsyncClient
from sqlalchemy import text

from app.modules.payments.bank import webhook_signature
from tests.scenario import World, build_world, fund_stage, post


async def _stage(client: AsyncClient, w: World, **extra) -> str:  # type: ignore[no-untyped-def]
    r = await post(
        client, f"/api/v1/objects/{w.object_id}/stages", w.estimator, {"title": "Стяжка", **extra}
    )
    return r.json()["id"]


async def _sign(client: AsyncClient, who: dict[str, str], act_id: str):  # type: ignore[no-untyped-def]
    req = await post(client, f"/api/v1/acts/{act_id}/sign/request", who)
    assert req.status_code == 200, req.text
    body = {"signature_id": req.json()["signature_id"], "code": req.json()["debug_code"]}
    return await post(client, f"/api/v1/acts/{act_id}/sign/confirm", who, body)


async def test_hold_rules(client: AsyncClient, admin: dict[str, str], engine) -> None:  # type: ignore[no-untyped-def]
    w = await build_world(client, admin)
    sid = await _stage(client, w)
    url = f"/api/v1/stages/{sid}/payments/hold"
    assert (await post(client, url, w.customer)).status_code == 409  # смета не утверждена
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE objects SET status = 'contracting' WHERE id = :id"), {"id": w.object_id}
        )
    assert (await post(client, url, w.customer)).json()["type"].endswith("stage-no-budget")
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE stages SET work_budget_kop = 500000 WHERE id = :id"), {"id": sid}
        )
    assert (await post(client, url, w.boss)).status_code == 403
    first = (await post(client, url, w.customer)).json()
    assert first["status"] == "pending_funding" and first["pay_url"].endswith(first["id"])
    assert first["breakdown"] == {"work_kop": 500000, "material_kop": 0, "platform_fee_kop": 0}
    again = (await post(client, url, w.customer)).json()
    assert again["id"] == first["id"]

    # Отказ от оплаты — можно выставить счёт заново.
    pay = f"/api/v1/fake-bank/payments/{first['id']}/pay"
    assert (await post(client, pay, w.boss, {"success": True})).status_code == 403
    failed = (await post(client, pay, w.customer, {"success": False})).json()
    assert failed["status"] == "failed"
    second = (await post(client, url, w.customer)).json()
    assert second["id"] != first["id"]

    # Вебхук банка: без подписи не принимается, с подписью — резерв проведён, повтор безвреден.
    body = json.dumps({"event_id": "e1", "payment_id": second["id"], "status": "held"}).encode()
    hook = "/api/v1/webhooks/bank"
    bad = await client.post(hook, content=body, headers={"X-Bank-Signature": "nope"})
    assert bad.status_code == 401
    for _ in range(2):
        ok = await client.post(
            hook, content=body, headers={"X-Bank-Signature": webhook_signature(body)}
        )
        assert ok.json() == {"ok": True}
    unknown = json.dumps(
        {"event_id": "e2", "payment_id": str(uuid.uuid4()), "status": "held"}
    ).encode()
    r = await client.post(
        hook, content=unknown, headers={"X-Bank-Signature": webhook_signature(unknown)}
    )
    assert r.json() == {"ok": False}
    stage = (await client.get(f"/api/v1/stages/{sid}", headers=w.customer)).json()
    assert stage["stage"]["status"] == "ready"
    payments = (await client.get(f"/api/v1/stages/{sid}/payments", headers=w.boss)).json()
    assert [p["status"] for p in payments] == ["held", "failed"]
    assert (await post(client, url, w.customer)).status_code == 409


async def test_act_sign_and_release(client: AsyncClient, admin: dict[str, str], engine) -> None:  # type: ignore[no-untyped-def]
    w = await build_world(client, admin)
    sid = await _stage(client, w)
    hold = await fund_stage(client, engine, w, sid, work_kop=3_000_000, material_kop=1_000_000)
    acts = f"/api/v1/stages/{sid}/acts"
    assert (await post(client, acts, w.boss)).status_code == 409  # этап ещё не сдан
    await post(client, f"/api/v1/stages/{sid}/start", w.boss)
    await post(client, f"/api/v1/stages/{sid}/submit", w.boss)
    assert (await post(client, acts, w.foreman)).status_code == 403
    act = (await post(client, acts, w.boss)).json()
    assert act["status"] == "sent" and act["number"] == "1-1"
    assert (await post(client, acts, w.boss)).status_code == 409

    # Сначала подписывает подрядчик, потом заказчик.
    assert (
        await post(client, f"/api/v1/acts/{act['id']}/sign/request", w.customer)
    ).status_code == 403
    req = (await post(client, f"/api/v1/acts/{act['id']}/sign/request", w.boss)).json()
    wrong = await post(
        client,
        f"/api/v1/acts/{act['id']}/sign/confirm",
        w.boss,
        {
            "signature_id": req["signature_id"],
            "code": "000000" if req["debug_code"] != "000000" else "111111",
        },
    )
    assert wrong.status_code == 400
    from app.core.redis import get_redis

    await get_redis().delete(f"sign:cool:{w.user_ids['contractor_admin']}:{act['id']}")
    signed = await _sign(client, w.boss, act["id"])
    assert signed.json()["status"] == "signed_contractor"

    # Заказчик отклоняет: этап возвращается в работу.
    reject = await post(
        client, f"/api/v1/acts/{act['id']}/reject", w.customer, {"reason": "Кривой угол"}
    )
    assert reject.json()["status"] == "rejected"
    card = (await client.get(f"/api/v1/stages/{sid}", headers=w.customer)).json()
    assert card["stage"]["status"] == "in_progress"

    await post(client, f"/api/v1/stages/{sid}/submit", w.boss)
    act2 = (await post(client, acts, w.boss)).json()
    assert act2["number"] == "1-2"
    await _sign(client, w.boss, act2["id"])
    done = (await _sign(client, w.customer, act2["id"])).json()
    assert done["status"] == "released"
    assert done["payment_status"] == "released"
    assert done["contractor_signed_at"] and done["customer_signed_at"]
    card = (await client.get(f"/api/v1/stages/{sid}", headers=w.customer)).json()
    assert card["stage"]["status"] == "paid"
    payment = (await client.get(f"/api/v1/payments/{hold['id']}", headers=w.customer)).json()
    assert payment["recipients"] == [
        {"role": "contractor", "inn": "7707083893", "amount_kop": 4_000_000}
    ]

    # Повторной выплаты по тому же акту не бывает.
    again = await post(
        client, f"/api/v1/stages/{sid}/payments/release", admin, {"act_id": act2["id"]}
    )
    assert again.status_code == 409
    assert (
        await post(client, f"/api/v1/stages/{sid}/payments/release", w.boss, {"act_id": act2["id"]})
    ).status_code == 403
    log = (await client.get("/api/v1/admin/audit-logs?entity_type=act", headers=admin)).json()
    actions = {e["action"] for e in log["items"]}
    assert {
        "act.created",
        "act.signed_contractor",
        "act.signed_customer",
        "act.released",
    } <= actions


async def test_act_needs_passed_stop_point(
    client: AsyncClient, admin: dict[str, str], engine
) -> None:  # type: ignore[no-untyped-def]
    w = await build_world(client, admin)
    sid = await _stage(client, w, is_stop_point=True)
    await fund_stage(client, engine, w, sid)
    await post(client, f"/api/v1/stages/{sid}/start", w.boss)
    await post(client, f"/api/v1/stages/{sid}/submit", w.boss)
    async with engine.begin() as conn:  # обходим технадзор напрямую, чтобы проверить защиту
        await conn.execute(
            text("UPDATE stages SET status = 'acceptance' WHERE id = :id"), {"id": sid}
        )
    r = await post(client, f"/api/v1/stages/{sid}/acts", w.boss)
    assert r.json()["type"].endswith("stop-point-not-passed")
