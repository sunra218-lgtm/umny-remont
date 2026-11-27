"""Т-Банк: безопасная сделка через интернет-эквайринг и выплаты (ТЗ 2.3, раздел 7).

Как это устроено у банка:

* **Резерв.** `Init` на терминале приёма оплаты с `CreateDealWithType=NN` открывает сделку.
  Заказчик платит по ссылке `PaymentURL` (СБП или карта). Деньги ложатся на счёт сделки
  у банка, платформа их не держит. Уведомление `CONFIRMED` = резерв проведён; в нём номер
  сделки `SpAccumulationId`, его сохраняем у платежа.
* **Выплата по акту.** На терминале выплат (e2c) для каждого получателя `Init` с `DealId`,
  `PartnerId` и суммой, затем `Payment`. Последняя выплата помечается `FinalPayout` —
  банк закрывает сделку. Итог приходит уведомлением `COMPLETED` / `REJECTED`.
* **Заморозка на время спора** — решение платформы: пока не отправлена выплата, деньги и так
  лежат на счёте сделки. Запрос в банк не нужен.
* **Возврат заказчику** — `Cancel` по операции оплаты.

Подпись запросов и уведомлений — поле `Token`: SHA-256 от склеенных значений полей верхнего
уровня (вложенные объекты не участвуют) плюс пароль терминала, по алфавиту ключей.

Номера заказов (`OrderId`) у банка ограничены по длине, поэтому берём uuid платежа без
дефисов (32 знака). Выплатам добавляем три знака: попытка, номер получателя, признак
последней выплаты (`f`) или промежуточной (`p`).

Поля API сверены с открытыми описаниями Т-Банка; перед пилотом — прогон на тестовых
терминалах (ключи с окончанием DEMO), см. docs/tbank.md.
"""

import hashlib
import hmac
import json
import logging
import uuid
from collections.abc import Mapping
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import httpx

from app.core.config import get_settings
from app.core.problems import Problem
from app.core.security import utcnow
from app.modules.payments.bank import (
    BankEvent,
    HoldResult,
    Recipient,
    WebhookResult,
    internal_event,
)

if TYPE_CHECKING:
    from app.models import Payment

log = logging.getLogger(__name__)

ATTEMPTS = "0123456789abcdefghijklmnopqrstuvwxyz"
# Статусы операции оплаты → события платформы.
PAY_STATUS = {
    "CONFIRMED": "held",
    "REJECTED": "failed",
    "DEADLINE_EXPIRED": "failed",
    "AUTH_FAIL": "failed",
    "CANCELED": "failed",
    "REVERSED": "failed",
    "REFUNDED": "refunded",
}
FAIL_REASON = {
    "REJECTED": "Банк отклонил оплату",
    "DEADLINE_EXPIRED": "Истёк срок оплаты",
    "AUTH_FAIL": "Оплата не подтверждена",
    "CANCELED": "Оплата отменена",
    "REVERSED": "Оплата отменена",
}


def make_token(params: Mapping[str, Any], password: str) -> str:
    """Подпись Т-Банка: только поля верхнего уровня, без вложенных объектов и самого Token."""
    pairs: dict[str, str] = {"Password": password}
    for key, value in params.items():
        if key == "Token" or value is None or isinstance(value, dict | list):
            continue
        pairs[key] = ("true" if value else "false") if isinstance(value, bool) else str(value)
    joined = "".join(pairs[k] for k in sorted(pairs))
    return hashlib.sha256(joined.encode()).hexdigest()


def order_id(payment_id: uuid.UUID) -> str:
    return payment_id.hex


def payout_order_id(payment_id: uuid.UUID, attempt: int, index: int, final: bool) -> str:
    return f"{payment_id.hex}{ATTEMPTS[attempt % len(ATTEMPTS)]}{index}{'f' if final else 'p'}"


def payment_id_from(order: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(hex=order[:32])
    except ValueError:
        return None


def _bank(payment: "Payment") -> dict[str, Any]:
    return dict((payment.bank_payload or {}).get("bank", {}))


class TBankError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class TBankGateway:
    name = "tbank"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    # --- HTTP -------------------------------------------------------------------------------

    async def _call(
        self, base: str, terminal: str, password: str, method: str, **params: Any
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "TerminalKey": terminal,
            **{k: v for k, v in params.items() if v is not None},
        }
        body["Token"] = make_token(body, password)
        s = get_settings()
        try:
            async with httpx.AsyncClient(
                timeout=s.tbank_timeout_s, transport=self._transport
            ) as client:
                resp = await client.post(f"{base.rstrip('/')}/{method}", json=body)
            data: dict[str, Any] = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("tbank %s: нет ответа: %s", method, exc)
            raise TBankError("network", "Банк не ответил") from exc
        if not data.get("Success"):
            code = str(data.get("ErrorCode", "unknown"))
            text = str(data.get("Message") or data.get("Details") or "Ошибка банка")
            log.warning("tbank %s: ошибка %s %s", method, code, text)
            raise TBankError(code, text)
        return data

    def _acq(self) -> tuple[str, str, str]:
        s = get_settings()
        assert s.tbank_terminal_key and s.tbank_password
        return s.tbank_api_url, s.tbank_terminal_key, s.tbank_password

    def _e2c(self) -> tuple[str, str, str]:
        s = get_settings()
        assert s.tbank_payout_terminal_key and s.tbank_payout_password
        return s.tbank_payout_api_url, s.tbank_payout_terminal_key, s.tbank_payout_password

    @staticmethod
    def _hook_url() -> str:
        return f"{get_settings().public_base_url.rstrip('/')}/api/v1/webhooks/bank"

    # --- Операции ---------------------------------------------------------------------------

    async def create_hold(self, payment_id: uuid.UUID, amount_kop: int, purpose: str) -> HoldResult:
        s = get_settings()
        expires = utcnow() + timedelta(seconds=s.bank_hold_ttl_s)
        site = s.public_base_url.rstrip("/")
        try:
            data = await self._call(
                *self._acq(),
                "Init",
                Amount=amount_kop,
                OrderId=order_id(payment_id),
                Description=purpose[:140],
                PayType="O",
                CreateDealWithType="NN",
                NotificationURL=self._hook_url(),
                SuccessURL=f"{site}/pay/{payment_id}?result=success",
                FailURL=f"{site}/pay/{payment_id}?result=fail",
                RedirectDueDate=expires.replace(microsecond=0).isoformat(),
            )
        except TBankError as exc:
            raise Problem(
                502, "bank-unavailable", "Банк не принял счёт", "Попробуйте ещё раз через минуту."
            ) from exc
        return HoldResult(
            bank_op_id=str(data["PaymentId"]),
            pay_url=str(data["PaymentURL"]),
            expires_at=expires,
        )

    async def release(self, payment: "Payment", recipients: list[Recipient]) -> BankEvent | None:
        bank = _bank(payment)
        deal = bank.get("deal_id")
        attempt = int(bank.get("payout_attempt", -1)) + 1
        payment.bank_payload = {
            **(payment.bank_payload or {}),
            "bank": {**bank, "payout_attempt": attempt},
        }

        def failed(reason: str) -> BankEvent:
            return BankEvent(
                f"tbank-payout-{payment.id}-{attempt}-failed",
                payment.id,
                "failed",
                payload={"reason": reason},
            )

        if not deal:
            return failed("Банк не прислал номер сделки по резерву")
        s = get_settings()
        partners = [
            r.partner_id or (s.tbank_platform_partner_id if r.role == "platform" else None)
            for r in recipients
        ]
        if not all(partners):
            return failed("Получатель не зарегистрирован в банке для выплат")
        last = len(recipients) - 1
        for index, (recipient, partner) in enumerate(zip(recipients, partners, strict=True)):
            try:
                init = await self._call(
                    *self._e2c(),
                    "Init",
                    Amount=recipient.amount_kop,
                    OrderId=payout_order_id(payment.id, attempt, index, index == last),
                    DealId=deal,
                    PartnerId=partner,
                    FinalPayout=True if index == last else None,
                    NotificationURL=self._hook_url(),
                )
                await self._call(*self._e2c(), "Payment", PaymentId=init["PaymentId"])
            except TBankError as exc:
                # Уже отправленные выплаты банк проведёт; повтор начнёт новую попытку.
                return failed(f"Выплата не прошла: {exc.message}")
        return None

    async def freeze(self, payment: "Payment") -> BankEvent | None:
        return internal_event(payment, "frozen")

    async def unfreeze(self, payment: "Payment") -> BankEvent | None:
        return internal_event(payment, "unfrozen")

    async def refund(self, payment: "Payment", amount_kop: int) -> BankEvent | None:
        bank_payment = _bank(payment).get("payment_id") or payment.bank_op_id
        try:
            data = await self._call(
                *self._acq(), "Cancel", PaymentId=bank_payment, Amount=amount_kop
            )
        except TBankError as exc:
            raise Problem(
                502,
                "bank-unavailable",
                "Банк не провёл возврат",
                "Попробуйте ещё раз через минуту.",
            ) from exc
        status = str(data.get("Status", ""))
        if status in ("REFUNDED", "PARTIAL_REFUNDED", "REVERSED", "QUEUED"):
            return BankEvent(
                f"tbank-refund-{payment.id}",
                payment.id,
                "refunded",
                payload={"amount_kop": amount_kop},
            )
        raise Problem(502, "bank-unavailable", "Банк не провёл возврат", f"Статус банка: {status}")

    # --- Уведомления ------------------------------------------------------------------------

    def parse_webhook(self, body: bytes, headers: Mapping[str, str]) -> WebhookResult | None:
        try:
            data = json.loads(body)
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        s = get_settings()
        terminal = data.get("TerminalKey")
        if terminal and terminal == s.tbank_terminal_key:
            password, kind = s.tbank_password, "pay"
        elif terminal and terminal == s.tbank_payout_terminal_key:
            password, kind = s.tbank_payout_password, "payout"
        else:
            return None
        token = str(data.get("Token", ""))
        if not password or not hmac.compare_digest(token, make_token(data, password)):
            return None
        status = str(data.get("Status", ""))
        pid = payment_id_from(str(data.get("OrderId", "")))
        if pid is None:
            return WebhookResult(None)
        bank_id = str(data.get("PaymentId", ""))
        event_id = f"tbank-{kind}-{bank_id}-{status}"
        if kind == "pay":
            mapped = PAY_STATUS.get(status)
            if mapped is None:
                return WebhookResult(None)  # промежуточный статус (NEW, AUTHORIZED и т. п.)
            store: dict[str, Any] = {"payment_id": bank_id}
            deal = data.get("SpAccumulationId") or data.get("DealId")
            if deal:
                store["deal_id"] = str(deal)
            return WebhookResult(
                BankEvent(
                    event_id,
                    pid,
                    mapped,
                    bank_op_id=bank_id,
                    payload={"reason": FAIL_REASON.get(status), "bank_status": status},
                    store=store,
                )
            )
        order = str(data.get("OrderId", ""))
        if status == "REJECTED":
            return WebhookResult(
                BankEvent(
                    event_id,
                    pid,
                    "failed",
                    payload={"reason": "Банк отклонил выплату", "bank_status": status},
                )
            )
        if status == "COMPLETED" and order.endswith("f"):
            return WebhookResult(
                BankEvent(event_id, pid, "released", bank_op_id=f"payout-{bank_id}")
            )
        return WebhookResult(None)


def notification(params: dict[str, Any], password: str) -> dict[str, Any]:
    """Уведомление в формате Т-Банка с подписью — для тестов и ручной проверки."""
    return {**params, "Token": make_token(params, password)}
