"""Спринт 6: споры с заморозкой денег, консьерж-закупки, приёмка доставок, уведомления."""

import json
from datetime import timedelta
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.core.security import utcnow
from app.core.storage import LocalStorage, set_storage
from tests.conftest import idem, login
from tests.scenario import World, build_world, fund_stage, post
from tests.test_field import foreman_on_phone, photo

MANAGER = "+79160000006"
SUPPLIER = "+79160000007"


@pytest.fixture(autouse=True)
def media_dir(tmp_path: Path) -> None:
    set_storage(LocalStorage(str(tmp_path)))


async def _stage(client: AsyncClient, w: World, title: str = "Стяжка") -> str:
    r = await post(client, f"/api/v1/objects/{w.object_id}/stages", w.estimator, {"title": title})
    return r.json()["id"]


async def _sign(client: AsyncClient, who: dict[str, str], act_id: str):  # type: ignore[no-untyped-def]
    req = await post(client, f"/api/v1/acts/{act_id}/sign/request", who)
    assert req.status_code == 200, req.text
    body = {"signature_id": req.json()["signature_id"], "code": req.json()["debug_code"]}
    return await post(client, f"/api/v1/acts/{act_id}/sign/confirm", who, body)


async def _status(client: AsyncClient, w: World, sid: str) -> tuple[str, str]:
    card = (await client.get(f"/api/v1/stages/{sid}", headers=w.customer)).json()
    return card["stage"]["status"], card["object_status"]


async def _notes(engine, template: str) -> int:  # type: ignore[no-untyped-def]
    async with engine.begin() as conn:
        return (
            await conn.execute(
                text("SELECT count(*) FROM notifications WHERE template = :t"), {"t": template}
            )
        ).scalar_one()


async def test_dispute_freeze_and_continue(  # type: ignore[no-untyped-def]
    client: AsyncClient, admin: dict[str, str], engine
) -> None:
    w = await build_world(client, admin)
    sid = await _stage(client, w)
    hold = await fund_stage(client, engine, w, sid)
    await post(client, f"/api/v1/stages/{sid}/start", w.boss)
    await post(client, f"/api/v1/stages/{sid}/submit", w.boss)
    act = (await post(client, f"/api/v1/stages/{sid}/acts", w.boss)).json()
    await _sign(client, w.boss, act["id"])

    url = f"/api/v1/objects/{w.object_id}/disputes"
    body = {"stage_id": sid, "reason": "Стяжка неровная, перепад 2 см"}
    assert (await post(client, url, w.foreman, body)).status_code == 403
    assert (await post(client, url, w.customer, {**body, "reason": "плохо"})).status_code == 422
    opened = await post(client, url, w.customer, body)
    assert opened.status_code == 201, opened.text
    d = opened.json()
    assert d["status"] == "open" and d["stage_status_before"] == "acceptance"
    assert d["payment_status"] == "frozen"
    assert (await post(client, url, w.boss, body)).json()["type"].endswith("dispute-open")
    assert await _status(client, w, sid) == ("frozen", "dispute")
    assert await _notes(engine, "dispute_opened") >= 3  # заказчик, подрядчик, админ

    # Пока спор открыт, подписать акт и получить деньги нельзя.
    blocked = await post(client, f"/api/v1/acts/{act['id']}/sign/request", w.customer)
    assert blocked.json()["type"].endswith("dispute-open")
    retry = await post(
        client, f"/api/v1/stages/{sid}/payments/release", admin, {"act_id": act["id"]}
    )
    assert retry.status_code == 409

    # Разбирает только администратор.
    assert (await client.get("/api/v1/disputes", headers=w.customer)).status_code == 403
    listed = (await client.get("/api/v1/disputes", headers=admin)).json()
    assert [x["id"] for x in listed] == [d["id"]]
    taken = await post(client, f"/api/v1/disputes/{d['id']}/review", admin)
    assert taken.json()["status"] == "in_review"
    resolve = f"/api/v1/disputes/{d['id']}/resolve"
    decision = {"outcome": "continue", "resolution": "Перепад в допуске СП, работы приняты"}
    assert (await post(client, resolve, w.customer, decision)).status_code == 403
    done = (await post(client, resolve, admin, decision)).json()
    assert done["status"] == "resolved" and done["outcome"] == "continue"
    assert done["payment_status"] == "held"
    assert await _status(client, w, sid) == ("acceptance", "active")
    assert (await post(client, resolve, admin, decision)).status_code == 409
    assert (await client.get("/api/v1/disputes", headers=admin)).json() == []

    # После спора заказчик подписывает акт, деньги уходят.
    final = (await _sign(client, w.customer, act["id"])).json()
    assert final["status"] == "released"
    hist = (await client.get(url, headers=w.boss)).json()
    assert len(hist) == 1
    payment = (await client.get(f"/api/v1/payments/{hold['id']}", headers=w.customer)).json()
    assert payment["status"] == "released"
    async with engine.begin() as conn:
        actions = (
            await conn.execute(
                text("SELECT action FROM audit_logs WHERE entity_id = :id ORDER BY id"),
                {"id": hold["id"]},
            )
        ).scalars()
        assert "payment.frozen" in list(actions)
    # По выплаченному этапу спор уже не открыть.
    late = await post(client, url, w.customer, body)
    assert late.json()["type"].endswith("stage-not-disputable")


async def test_dispute_refund(client: AsyncClient, admin: dict[str, str], engine) -> None:  # type: ignore[no-untyped-def]
    w = await build_world(client, admin)
    sid = await _stage(client, w)
    other = await _stage(client, w, "Плитка")
    hold = await fund_stage(client, engine, w, sid)
    await post(client, f"/api/v1/stages/{sid}/start", w.boss)
    await post(client, f"/api/v1/stages/{sid}/submit", w.boss)
    act = (await post(client, f"/api/v1/stages/{sid}/acts", w.boss)).json()
    await _sign(client, w.boss, act["id"])
    url = f"/api/v1/objects/{w.object_id}/disputes"
    # Этап без резерва (planned) оспорить нельзя.
    reason = "Работы не начаты вовремя"
    early = await post(client, url, w.customer, {"stage_id": other, "reason": reason})
    assert early.status_code == 409
    d = (await post(client, url, w.boss, {"stage_id": sid, "reason": "Заказчик не пускает"})).json()
    done = await post(
        client,
        f"/api/v1/disputes/{d['id']}/resolve",
        admin,
        {"outcome": "refund", "resolution": "Договор расторгнут по соглашению"},
    )
    assert done.json()["payment_status"] == "refunded", done.text
    assert await _status(client, w, sid) == ("funding", "active")
    acts = (await client.get(f"/api/v1/stages/{sid}/acts", headers=w.customer)).json()
    assert acts[0]["status"] == "rejected"
    payment = (await client.get(f"/api/v1/payments/{hold['id']}", headers=w.customer)).json()
    assert payment["status"] == "refunded"
    # Деньги можно зарезервировать заново.
    again = await post(client, f"/api/v1/stages/{sid}/payments/hold", w.customer)
    assert again.status_code == 201 and again.json()["id"] != hold["id"]


async def _procurement_world(client: AsyncClient, admin: dict[str, str], engine):  # type: ignore[no-untyped-def]
    w = await build_world(client, admin)
    await post(
        client,
        f"/api/v1/organizations/{w.platform_org_id}/members",
        admin,
        {"phone": MANAGER, "role": "procurement_manager"},
    )
    sup = await post(
        client,
        "/api/v1/organizations",
        admin,
        {"type": "supplier", "inn": "7736050003", "legal_name": "ООО «Стройбаза»"},
    )
    supplier_id = sup.json()["id"]
    await post(
        client,
        f"/api/v1/organizations/{supplier_id}/members",
        admin,
        {"phone": SUPPLIER, "role": "supplier_manager"},
    )
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE objects SET status = 'active' WHERE id = :id"), {"id": w.object_id}
        )
    return w, await login(client, MANAGER), await login(client, SUPPLIER), supplier_id


def _receive(result: str, discrepancies=None, comment=None):  # type: ignore[no-untyped-def]
    data = {"result": result, "discrepancies": json.dumps(discrepancies or [])}
    if comment:
        data["comment"] = comment
    return {"data": data, "files": {"invoice": ("invoice.jpg", photo(7), "image/jpeg")}}


async def test_concierge_order_flow(client: AsyncClient, admin: dict[str, str], engine) -> None:  # type: ignore[no-untyped-def]
    w, manager, supplier, supplier_id = await _procurement_world(client, admin, engine)
    sid = await _stage(client, w)

    # Прораб (здесь руководитель подрядчика) присылает заявку текстом и голосом.
    req_url = f"/api/v1/objects/{w.object_id}/material-requests"
    empty = await client.post(req_url, data={"text": " "}, headers={**w.boss, **idem()})
    assert empty.status_code == 422
    stranger = await client.post(req_url, data={"text": "x"}, headers={**w.customer, **idem()})
    assert stranger.status_code == 403
    voice = await client.post(
        req_url,
        data={"text": "Цемент М500, 20 мешков", "stage_id": sid},
        files={"audio": ("voice.webm", b"\x1aE\xdf\xa3fake-webm", "audio/webm")},
        headers={**w.boss, **idem()},
    )
    assert voice.status_code == 201, voice.text
    request = voice.json()
    assert request["status"] == "new" and request["audio_url"]
    audio = await client.get(request["audio_url"])
    assert audio.status_code == 200 and audio.headers["content-type"].startswith("audio/webm")
    bad_link = await client.get(request["audio_url"].replace("sig=", "sig=0"))
    assert bad_link.status_code == 403
    assert await _notes(engine, "material_request") == 1
    assert (await client.get("/api/v1/material-requests", headers=w.boss)).status_code == 403
    inbox = (await client.get("/api/v1/material-requests", headers=manager)).json()
    assert [r["id"] for r in inbox] == [request["id"]]

    # Менеджер собирает корзину. В минус продавать нельзя.
    orders_url = f"/api/v1/objects/{w.object_id}/purchase-orders"
    basket = {
        "stage_id": sid,
        "request_id": request["id"],
        "supplier_org_id": supplier_id,
        "supplier_cost_kop": 900_000,
        "items": [
            {"title": "Цемент М500, 50 кг", "qty": "20", "unit_price_kop": 50_000},
        ],
    }
    assert (await post(client, orders_url, w.boss, basket)).status_code == 403
    minus = await post(client, orders_url, manager, {**basket, "supplier_cost_kop": 2_000_000})
    assert minus.json()["type"].endswith("negative-margin")
    order = (await post(client, orders_url, manager, basket)).json()
    assert order["status"] == "draft" and order["total_kop"] == 1_000_000
    assert order["margin_kop"] == 100_000 and order["can_edit"]
    oid = order["id"]
    # Черновик заказчик не видит.
    assert (await client.get(orders_url, headers=w.customer)).json() == []
    basket["items"].append({"title": "Пескобетон", "qty": "2.5", "unit_price_kop": 40_000})
    basket["supplier_cost_kop"] = 950_000
    updated = await client.put(
        f"/api/v1/purchase-orders/{oid}", json=basket, headers={**manager, **idem()}
    )
    assert updated.json()["total_kop"] == 1_100_000
    proposed = (await post(client, f"/api/v1/purchase-orders/{oid}/propose", manager)).json()
    assert proposed["status"] == "proposed" and proposed["expires_at"]
    assert await _notes(engine, "order_proposed") == 1

    seen = (await client.get(orders_url, headers=w.customer)).json()[0]
    assert seen["can_confirm"] and seen["supplier_cost_kop"] is None
    assert seen["margin_kop"] is None
    assert (await post(client, f"/api/v1/purchase-orders/{oid}/confirm", w.boss)).status_code == 403
    confirmed = (await post(client, f"/api/v1/purchase-orders/{oid}/confirm", w.customer)).json()
    assert confirmed["status"] == "confirmed"
    assert confirmed["payment"]["status"] == "pending_funding"
    assert confirmed["payment"]["pay_url"]
    # Заказать у поставщика можно только после оплаты.
    early = await post(client, f"/api/v1/purchase-orders/{oid}/order", manager)
    assert early.json()["type"].endswith("not-paid")
    pid = confirmed["payment"]["id"]
    paid = await post(client, f"/api/v1/fake-bank/payments/{pid}/pay", w.customer, {})
    assert paid.json()["status"] == "held"
    assert await _notes(engine, "order_paid") >= 1
    # Резерв материалов не трогает статус этапа.
    assert (await _status(client, w, sid))[0] == "planned"

    ordered = (await post(client, f"/api/v1/purchase-orders/{oid}/order", manager)).json()
    assert ordered["status"] == "ordered" and ordered["can_ship"]

    # Поставщик видит свой заказ и отмечает отгрузку.
    mine = (await client.get("/api/v1/purchase-orders", headers=supplier)).json()
    assert [o["id"] for o in mine] == [oid] and mine[0]["supplier_cost_kop"] is None
    assert (await client.get("/api/v1/purchase-orders", headers=w.customer)).status_code == 403
    slot = (utcnow() + timedelta(days=1)).isoformat()
    shipped = await post(
        client, f"/api/v1/purchase-orders/{oid}/ship", supplier, {"delivery_slot_at": slot}
    )
    assert shipped.json()["status"] == "delivering", shipped.text
    assert await _notes(engine, "order_shipped") >= 1

    # Прораб принимает с расхождением: не хватает мешков.
    deliveries = f"/api/v1/purchase-orders/{oid}/deliveries"
    nope = await client.post(deliveries, **_receive("discrepancies"), headers={**w.boss, **idem()})
    assert nope.json()["type"].endswith("no-discrepancies")
    by_customer = await client.post(
        deliveries, **_receive("accepted"), headers={**w.customer, **idem()}
    )
    assert by_customer.status_code == 403
    gap = [{"title": "Цемент М500, 50 кг", "expected": "20", "received": "18"}]
    partial = await client.post(
        deliveries, **_receive("discrepancies", gap), headers={**w.boss, **idem()}
    )
    assert partial.status_code == 201, partial.text
    p = partial.json()
    assert p["status"] == "partially_delivered"
    assert p["deliveries"][0]["status"] == "accepted_with_discrepancies"
    invoice = await client.get(p["deliveries"][0]["invoice_url"])
    assert invoice.status_code == 200 and invoice.headers["content-type"] == "image/jpeg"
    assert await _notes(engine, "delivery_problem") == 1

    # Довезли остаток — второй приёмкой заказ закрыт, деньги ушли поставщику и платформе.
    await post(client, f"/api/v1/purchase-orders/{oid}/ship", manager, {})
    full = await client.post(deliveries, **_receive("accepted"), headers={**w.boss, **idem()})
    f = full.json()
    assert f["status"] == "delivered" and f["payment"]["status"] == "released"
    payment = (await client.get(f"/api/v1/payments/{pid}", headers=w.customer)).json()
    assert payment["recipients"] == [
        {"role": "supplier", "inn": "7736050003", "amount_kop": 950_000},
        {"role": "platform", "inn": payment["recipients"][1]["inn"], "amount_kop": 150_000},
    ]
    req_after = (await client.get(req_url, headers=w.boss)).json()[0]
    assert req_after["status"] == "done"
    assert await _notes(engine, "order_delivered") >= 1
    again = await client.post(deliveries, **_receive("accepted"), headers={**w.boss, **idem()})
    assert again.json()["type"].endswith("not-shipped")


async def test_order_reject_expire_and_complete(  # type: ignore[no-untyped-def]
    client: AsyncClient, admin: dict[str, str], engine
) -> None:
    w, manager, _, _ = await _procurement_world(client, admin, engine)
    orders_url = f"/api/v1/objects/{w.object_id}/purchase-orders"
    basket = {"supplier_cost_kop": 0, "items": [{"title": "Грунт", "qty": 1, "unit_price_kop": 1}]}

    async def proposed() -> str:
        oid = (await post(client, orders_url, manager, basket)).json()["id"]
        await post(client, f"/api/v1/purchase-orders/{oid}/propose", manager)
        return oid

    # Отказ заказчика.
    first = await proposed()
    rejected = await post(
        client, f"/api/v1/purchase-orders/{first}/reject", w.customer, {"reason": "Куплю сам"}
    )
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["reject_reason"] == "Куплю сам"

    # 24 часа прошли: подтверждение не принимается, корзина сгорает.
    late = await proposed()
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE purchase_orders SET proposed_at = now() - interval '25 hours'")
        )
    expired = await post(client, f"/api/v1/purchase-orders/{late}/confirm", w.customer)
    assert expired.json()["type"].endswith("order-expired")
    got = (await client.get(f"/api/v1/purchase-orders/{late}", headers=w.customer)).json()
    assert got["status"] == "expired"

    from app.core.db import get_session
    from app.modules.procurement.router import expire_orders

    third = await proposed()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE purchase_orders SET proposed_at = now() - interval '2 days' WHERE id = :i"
            ),
            {"i": third},
        )
    async for db in get_session():
        assert await expire_orders(db) == 1

    # Без поставщика: всё уходит платформе. Расхождение закрывает менеджер.
    oid = await proposed()
    confirmed = (await post(client, f"/api/v1/purchase-orders/{oid}/confirm", w.customer)).json()
    pid = confirmed["payment"]["id"]
    await post(client, f"/api/v1/fake-bank/payments/{pid}/pay", w.customer, {})
    await post(client, f"/api/v1/purchase-orders/{oid}/order", manager)
    await post(client, f"/api/v1/purchase-orders/{oid}/ship", manager, {})
    deliveries = f"/api/v1/purchase-orders/{oid}/deliveries"
    no_reason = await client.post(deliveries, **_receive("rejected"), headers={**w.boss, **idem()})
    assert no_reason.json()["type"].endswith("no-comment")
    refused = await client.post(
        deliveries, **_receive("rejected", comment="Мокрые мешки"), headers={**w.boss, **idem()}
    )
    assert refused.json()["status"] == "delivering"
    gap = [{"title": "Грунт", "comment": "Не тот цвет"}]
    await client.post(deliveries, **_receive("discrepancies", gap), headers={**w.boss, **idem()})
    assert (
        await post(client, f"/api/v1/purchase-orders/{oid}/complete", w.boss)
    ).status_code == 403
    closed = (await post(client, f"/api/v1/purchase-orders/{oid}/complete", manager)).json()
    assert closed["status"] == "delivered" and len(closed["deliveries"]) == 2
    payment = (await client.get(f"/api/v1/payments/{pid}", headers=w.customer)).json()
    assert [r["role"] for r in payment["recipients"]] == ["platform"]
    listed = (await client.get("/api/v1/purchase-orders", headers=manager)).json()
    assert listed == []
    everything = await client.get("/api/v1/purchase-orders?active_only=false", headers=manager)
    assert len(everything.json()) == 4


async def test_notifications_queue_and_reminders(  # type: ignore[no-untyped-def]
    client: AsyncClient, admin: dict[str, str], engine, monkeypatch
) -> None:
    from app.core.db import get_session
    from app.jobs import daily_reminders
    from app.modules.notifications.service import send_queued

    w = await build_world(client, admin)
    await foreman_on_phone(client, w)
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE objects SET status = 'active' WHERE id = :id"), {"id": w.object_id}
        )
    async for db in get_session():
        assert await daily_reminders(db) == 1
    async for db in get_session():
        assert await daily_reminders(db) == 0  # одно напоминание в день
    sent: list[tuple[str, str]] = []

    class Sender:
        async def send(self, phone: str, message: str) -> None:
            if "сломан" in sent_flags:
                raise RuntimeError("провайдер недоступен")
            sent.append((phone, message))

    sent_flags: set[str] = set()
    monkeypatch.setattr("app.modules.notifications.service.get_sms_sender", lambda: Sender())
    async for db in get_session():
        assert await send_queued(db) >= 1
    assert any("Пришлите фотоотчёт" in m and "/lite" in m for _, m in sent)
    mine = (await client.get("/api/v1/me/notifications", headers=w.foreman)).json()
    assert mine[0]["status"] == "sent" and "фотоотчёт" in mine[0]["text"]
    async for db in get_session():
        assert await send_queued(db) == 0

    # Ошибка провайдера не роняет очередь: уведомление помечается как неотправленное.
    sent_flags.add("сломан")
    from app.modules.notifications.service import notify

    async for db in get_session():
        notify(db, [w.user_ids["foreman"]], "order_shipped", address="тест")
        await db.commit()
    async for db in get_session():
        assert await send_queued(db) == 1
    failed = (await client.get("/api/v1/me/notifications", headers=w.foreman)).json()
    assert failed[0]["status"] == "failed"
