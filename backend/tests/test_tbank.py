"""Спринт 8: адаптер Т-Банка — подпись, резерв, уведомления, выплата, возврат."""

import json
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.core.config import get_settings
from app.core.problems import Problem
from app.models import Payment
from app.modules.payments.bank import Recipient, set_bank
from app.modules.payments.tbank import (
    TBankGateway,
    make_token,
    notification,
    order_id,
    payout_order_id,
)
from tests.scenario import World, build_world, post

PAY_KEY, PAY_PASS = "UmnyDEMO", "pay-secret"
OUT_KEY, OUT_PASS = "UmnyE2CDEMO", "payout-secret"
HOOK = "/api/v1/webhooks/bank"


def test_token_matches_bank_example() -> None:
    """Пример из описания API банка: вложенный DATA в подписи не участвует."""
    params = {
        "TerminalKey": "MerchantTerminalKey",
        "Amount": 19200,
        "OrderId": "00000",
        "Description": "Подарочная карта на 1000 рублей",
        "DATA": {"Phone": "+71234567890"},
    }
    assert make_token(params, "11111111111111") == (
        "72dd466f8ace0a37a1f740ce5fb78101712bc0665d91a8108c7c8a0ccd426db2"
    )
    # Логические значения банк подписывает строками true/false.
    assert make_token({"A": True}, "p") == make_token({"A": "true"}, "p")


def test_payout_order_id_fits_bank_limit() -> None:
    pid = uuid.uuid4()
    oid = payout_order_id(pid, 40, 1, True)
    assert len(oid) <= 36 and oid.startswith(pid.hex) and oid.endswith("f")
    assert len(order_id(pid)) == 32


class FakeTBank:
    """Банк в тесте: проверяет подпись каждого запроса и отвечает как Т-Банк."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail: set[str] = set()
        self.cancel_status = "REFUNDED"

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        path = request.url.path
        payout = "/e2c/" in path
        method = path.rsplit("/", 1)[-1]
        password = OUT_PASS if payout else PAY_PASS
        assert body["TerminalKey"] == (OUT_KEY if payout else PAY_KEY)
        assert body["Token"] == make_token(body, password), "неверная подпись запроса"
        self.calls.append((("e2c:" if payout else "") + method, body))
        if method in self.fail:
            return httpx.Response(200, json={"Success": False, "ErrorCode": "99", "Message": "Нет"})
        if method == "Init" and not payout:
            return httpx.Response(
                200,
                json={
                    "Success": True,
                    "ErrorCode": "0",
                    "Status": "NEW",
                    "PaymentId": 700001,
                    "PaymentURL": "https://pay.tbank.ru/abc",
                },
            )
        if method == "Init":
            return httpx.Response(
                200,
                json={
                    "Success": True,
                    "ErrorCode": "0",
                    "Status": "NEW",
                    "PaymentId": 900000 + len(self.calls),
                },
            )
        if method == "Cancel":
            return httpx.Response(
                200, json={"Success": True, "ErrorCode": "0", "Status": self.cancel_status}
            )
        return httpx.Response(200, json={"Success": True, "ErrorCode": "0", "Status": "CHECKING"})


@pytest.fixture
def tbank(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeTBank]:
    s = get_settings()
    for key, value in {
        "bank_backend": "tbank",
        "tbank_terminal_key": PAY_KEY,
        "tbank_password": PAY_PASS,
        "tbank_payout_terminal_key": OUT_KEY,
        "tbank_payout_password": OUT_PASS,
        "tbank_platform_partner_id": "platform-1",
    }.items():
        monkeypatch.setattr(s, key, value)
    fake = FakeTBank()
    set_bank(TBankGateway(httpx.MockTransport(fake.handler)))
    yield fake
    set_bank(None)


async def _hook(client: AsyncClient, params: dict[str, Any], password: str) -> httpx.Response:
    return await client.post(HOOK, content=json.dumps(notification(params, password)).encode())


async def _sign(client: AsyncClient, who: dict[str, str], act_id: str) -> httpx.Response:
    req = (await post(client, f"/api/v1/acts/{act_id}/sign/request", who)).json()
    body = {"signature_id": req["signature_id"], "code": req["debug_code"]}
    return await post(client, f"/api/v1/acts/{act_id}/sign/confirm", who, body)


async def _hold(client: AsyncClient, engine, w: World) -> tuple[str, dict[str, Any]]:  # type: ignore[no-untyped-def]
    sid = (
        await post(client, f"/api/v1/objects/{w.object_id}/stages", w.estimator, {"title": "Стены"})
    ).json()["id"]
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE objects SET status = 'contracting' WHERE id = :id"), {"id": w.object_id}
        )
        await conn.execute(
            text("UPDATE stages SET work_budget_kop = 2500000 WHERE id = :id"), {"id": sid}
        )
    r = await post(client, f"/api/v1/stages/{sid}/payments/hold", w.customer)
    assert r.status_code == 201, r.text
    return sid, r.json()


async def test_full_cycle(
    client: AsyncClient, admin: dict[str, str], engine, tbank: FakeTBank
) -> None:  # type: ignore[no-untyped-def]
    w = await build_world(client, admin)
    sid, hold = await _hold(client, engine, w)
    assert hold["pay_url"] == "https://pay.tbank.ru/abc"
    method, init = tbank.calls[-1]
    assert method == "Init" and init["Amount"] == 2500000
    assert init["CreateDealWithType"] == "NN" and init["PayType"] == "O"
    assert init["OrderId"] == uuid.UUID(hold["id"]).hex
    assert init["NotificationURL"].endswith("/api/v1/webhooks/bank")
    # Тестовая оплата имитации при настоящем банке недоступна.
    fake_pay = await post(
        client, f"/api/v1/fake-bank/payments/{hold['id']}/pay", w.customer, {"success": True}
    )
    assert fake_pay.status_code == 404

    base = {
        "TerminalKey": PAY_KEY,
        "OrderId": init["OrderId"],
        "PaymentId": 700001,
        "Amount": 2500000,
        "ErrorCode": "0",
        "Success": True,
    }
    # Чужая подпись не принимается; промежуточный статус ничего не меняет.
    bad = await client.post(HOOK, content=json.dumps({**base, "Status": "CONFIRMED", "Token": "x"}))
    assert bad.status_code == 401
    r = await _hook(client, {**base, "Status": "AUTHORIZED"}, PAY_PASS)
    assert r.status_code == 200 and r.text == "OK"
    payment = (await client.get(f"/api/v1/payments/{hold['id']}", headers=w.customer)).json()
    assert payment["status"] == "pending_funding"
    for _ in range(2):  # банк может прислать уведомление повторно
        r = await _hook(
            client,
            {**base, "Status": "CONFIRMED", "SpAccumulationId": 555, "Data": {"x": 1}},
            PAY_PASS,
        )
        assert r.text == "OK"
    payment = (await client.get(f"/api/v1/payments/{hold['id']}", headers=w.customer)).json()
    assert payment["status"] == "held"
    card = (await client.get(f"/api/v1/stages/{sid}", headers=w.customer)).json()
    assert card["stage"]["status"] == "ready"

    # Работа сдана, акт подписан. Подрядчик ещё не зарегистрирован в банке — выплата не уходит.
    await post(client, f"/api/v1/stages/{sid}/start", w.boss)
    await post(client, f"/api/v1/stages/{sid}/submit", w.boss)
    act = (await post(client, f"/api/v1/stages/{sid}/acts", w.boss)).json()
    await _sign(client, w.boss, act["id"])
    done = (await _sign(client, w.customer, act["id"])).json()
    assert done["status"] == "signed_customer" and done["payment_status"] == "failed"
    assert not [c for c in tbank.calls if c[0].startswith("e2c:")]

    # Администратор вносит код получателя и повторяет выплату.
    patch = await client.patch(
        f"/api/v1/organizations/{w.org_id}",
        headers={**admin, "Idempotency-Key": str(uuid.uuid4())},
        json={"bank_partner_id": "contractor-7"},
    )
    assert patch.status_code == 200 and patch.json()["bank_partner_id"] == "contractor-7"
    retry = await post(
        client, f"/api/v1/stages/{sid}/payments/release", admin, {"act_id": act["id"]}
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["status"] == "release_requested"
    payouts = [c for c in tbank.calls if c[0].startswith("e2c:")]
    assert [m for m, _ in payouts] == ["e2c:Init", "e2c:Payment"]
    out = payouts[0][1]
    assert out["DealId"] == "555" and out["PartnerId"] == "contractor-7"
    assert out["Amount"] == 2500000 and out["FinalPayout"] is True
    assert out["OrderId"].startswith(uuid.UUID(hold["id"]).hex) and out["OrderId"].endswith("f")

    # Банк сообщает о зачислении — этап оплачен.
    payout_hook = {
        "TerminalKey": OUT_KEY,
        "OrderId": out["OrderId"],
        "PaymentId": 900001,
        "Status": "COMPLETED",
        "Success": True,
        "ErrorCode": "0",
    }
    assert (
        await _hook(client, {**payout_hook, "Status": "CREDIT_CHECKING"}, OUT_PASS)
    ).text == "OK"
    assert (
        await _hook(client, payout_hook, PAY_PASS)
    ).status_code == 401  # пароль не того терминала
    assert (await _hook(client, payout_hook, OUT_PASS)).text == "OK"
    payment = (await client.get(f"/api/v1/payments/{hold['id']}", headers=w.customer)).json()
    assert payment["status"] == "released"
    card = (await client.get(f"/api/v1/stages/{sid}", headers=w.customer)).json()
    assert card["stage"]["status"] == "paid"


async def test_hold_and_refund_errors(
    client: AsyncClient, admin: dict[str, str], engine, tbank: FakeTBank
) -> None:  # type: ignore[no-untyped-def]
    w = await build_world(client, admin)
    tbank.fail.add("Init")
    sid = (
        await post(client, f"/api/v1/objects/{w.object_id}/stages", w.estimator, {"title": "Пол"})
    ).json()["id"]
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE objects SET status = 'contracting' WHERE id = :id"), {"id": w.object_id}
        )
        await conn.execute(
            text("UPDATE stages SET work_budget_kop = 100000 WHERE id = :id"), {"id": sid}
        )
    r = await post(client, f"/api/v1/stages/{sid}/payments/hold", w.customer)
    assert r.status_code == 502 and r.json()["type"].endswith("bank-unavailable")

    gw = TBankGateway(httpx.MockTransport(tbank.handler))
    payment = Payment(
        id=uuid.uuid4(),
        amount_kop=100000,
        bank_op_id="700001",
        bank_payload={"bank": {"payment_id": "700001", "deal_id": "1"}},
    )
    event = await gw.refund(payment, 100000)
    assert event is not None and event.status == "refunded"
    assert tbank.calls[-1][0] == "Cancel" and tbank.calls[-1][1]["PaymentId"] == "700001"
    tbank.cancel_status = "REJECTED"
    with pytest.raises(Problem):
        await gw.refund(payment, 100000)
    tbank.fail.add("Cancel")
    with pytest.raises(Problem):
        await gw.refund(payment, 100000)
    # Заморозка — без запроса в банк.
    calls = len(tbank.calls)
    assert (await gw.freeze(payment)).status == "frozen"  # type: ignore[union-attr]
    assert (await gw.unfreeze(payment)).status == "unfrozen"  # type: ignore[union-attr]
    assert len(tbank.calls) == calls

    # Выплата: без номера сделки и при отказе банка — понятная ошибка, а не зависший платёж.
    no_deal = Payment(id=uuid.uuid4(), amount_kop=100, bank_payload={})
    ev = await gw.release(no_deal, [Recipient("platform", "1", 100)])
    assert ev is not None and ev.status == "failed"
    tbank.fail.add("Init")
    ev = await gw.release(
        payment, [Recipient("supplier", "1", 60, "s-1"), Recipient("platform", "2", 40)]
    )
    assert ev is not None and ev.status == "failed" and "Нет" in ev.payload["reason"]
    tbank.fail.clear()
    calls = len(tbank.calls)
    ev = await gw.release(
        payment, [Recipient("supplier", "1", 60, "s-1"), Recipient("platform", "2", 40)]
    )
    assert ev is None
    inits = [b for m, b in tbank.calls[calls:] if m == "e2c:Init"]
    assert [b["PartnerId"] for b in inits] == ["s-1", "platform-1"]
    assert "FinalPayout" not in inits[0] and inits[1]["FinalPayout"] is True
    assert inits[0]["OrderId"][32] != payout_order_id(payment.id, 0, 0, False)[32]  # новая попытка

    # Отказ в выплате — платёж в ошибку; неизвестный терминал и мусор — отказ в подписи.
    rejected = notification(
        {
            "TerminalKey": OUT_KEY,
            "OrderId": inits[0]["OrderId"],
            "Status": "REJECTED",
            "PaymentId": 1,
        },
        OUT_PASS,
    )
    parsed = gw.parse_webhook(json.dumps(rejected).encode(), {})
    assert parsed is not None and parsed.event is not None and parsed.event.status == "failed"
    assert gw.parse_webhook(b"not json", {}) is None
    assert (
        gw.parse_webhook(json.dumps(notification({"TerminalKey": "x"}, "y")).encode(), {}) is None
    )
    odd = notification({"TerminalKey": PAY_KEY, "OrderId": "zz", "Status": "CONFIRMED"}, PAY_PASS)
    parsed = gw.parse_webhook(json.dumps(odd).encode(), {})
    assert parsed is not None and parsed.event is None
