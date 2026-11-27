"""Банк безопасной сделки (ТЗ 2.3, раздел 7.1–7.2).

Платформа работает с банком через интерфейс `BankGateway`. Реализации:

* `FakeBank` — имитация для разработки и тестов: резерв «оплачивается» на тестовой странице
  сайта, выплата проходит сразу;
* `TBankGateway` (app/modules/payments/tbank.py) — Т-Банк, включается `BANK_BACKEND=tbank`.

Итоги операций банк присылает вебхуком `/webhooks/bank`; разбирает его тот же класс.
"""

import hashlib
import hmac
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol

from app.core.config import get_settings
from app.core.security import utcnow

if TYPE_CHECKING:
    from app.models import Payment


@dataclass
class Recipient:
    role: str  # contractor | supplier | platform
    inn: str
    amount_kop: int
    # Код получателя в банке (у Т-Банка — PartnerId после регистрации организации).
    partner_id: str | None = None

    def public(self) -> dict[str, Any]:
        """Что видно сторонам сделки: без служебного кода банка."""
        return {"role": self.role, "inn": self.inn, "amount_kop": self.amount_kop}


@dataclass
class HoldResult:
    bank_op_id: str
    pay_url: str
    expires_at: datetime


@dataclass
class BankEvent:
    """Итог операции банка: приходит вебхуком (или сразу — у имитации)."""

    event_id: str
    payment_id: uuid.UUID
    status: str  # held | released | failed | frozen | unfrozen | refunded
    bank_op_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    # Данные банка, которые нужно сохранить у платежа (номер операции, сделки и т. п.).
    store: dict[str, Any] = field(default_factory=dict)


@dataclass
class WebhookResult:
    """Разобранное уведомление банка и ответ, которого банк ждёт."""

    event: BankEvent | None
    reply: str = "OK"


class BankGateway(Protocol):
    name: str

    async def create_hold(self, payment_id: uuid.UUID, amount_kop: int, purpose: str) -> HoldResult:
        """Счёт на резерв: заказчик оплачивает его по ссылке (СБП/карта)."""
        ...

    async def release(self, payment: "Payment", recipients: list[Recipient]) -> BankEvent | None:
        """Выплата из резерва. Настоящий банк ответит вебхуком; имитация — сразу."""
        ...

    async def freeze(self, payment: "Payment") -> BankEvent | None:
        """Заморозка резерва на время спора: деньги не уходят ни одной стороне."""
        ...

    async def unfreeze(self, payment: "Payment") -> BankEvent | None:
        """Снятие заморозки: резерв снова доступен для выплаты по акту."""
        ...

    async def refund(self, payment: "Payment", amount_kop: int) -> BankEvent | None:
        """Возврат резерва заказчику."""
        ...

    def parse_webhook(self, body: bytes, headers: Mapping[str, str]) -> WebhookResult | None:
        """Проверка подписи и разбор уведомления. None — подпись не прошла."""
        ...


class FakeBank:
    name = "fake"

    async def create_hold(self, payment_id: uuid.UUID, amount_kop: int, purpose: str) -> HoldResult:
        s = get_settings()
        return HoldResult(
            bank_op_id=f"fake-hold-{payment_id}",
            pay_url=f"{s.public_base_url.rstrip('/')}/pay/{payment_id}",
            expires_at=utcnow() + timedelta(seconds=s.bank_hold_ttl_s),
        )

    async def release(self, payment: "Payment", recipients: list[Recipient]) -> BankEvent | None:
        return BankEvent(
            event_id=f"fake-release-{payment.id}",
            payment_id=payment.id,
            status="released",
            bank_op_id=f"fake-release-{payment.id}",
            payload={"recipients": [r.public() for r in recipients]},
        )

    async def freeze(self, payment: "Payment") -> BankEvent | None:
        return internal_event(payment, "frozen")

    async def unfreeze(self, payment: "Payment") -> BankEvent | None:
        return internal_event(payment, "unfrozen")

    async def refund(self, payment: "Payment", amount_kop: int) -> BankEvent | None:
        return BankEvent(
            f"fake-refund-{payment.id}", payment.id, "refunded", payload={"amount_kop": amount_kop}
        )

    def parse_webhook(self, body: bytes, headers: Mapping[str, str]) -> WebhookResult | None:
        signature = headers.get("x-bank-signature")
        if not signature or not hmac.compare_digest(signature, webhook_signature(body)):
            return None
        data = json.loads(body)
        event = BankEvent(
            event_id=str(data["event_id"]),
            payment_id=uuid.UUID(str(data["payment_id"])),
            status=str(data["status"]),
            bank_op_id=data.get("bank_op_id"),
            payload=data,
        )
        return WebhookResult(event, reply="")


def internal_event(payment: "Payment", status: str) -> BankEvent:
    """Заморозка у безопасной сделки — решение платформы, а не операция банка: деньги и так
    лежат на счёте сделки, пока платформа не отправит выплату."""
    return BankEvent(f"{status}-{payment.id}-{utcnow().timestamp()}", payment.id, status)


_bank: BankGateway | None = None


def get_bank() -> BankGateway:
    global _bank
    if _bank is None:
        if get_settings().bank_backend == "tbank":
            from app.modules.payments.tbank import TBankGateway

            _bank = TBankGateway()
        else:
            _bank = FakeBank()
    return _bank


def set_bank(bank: BankGateway | None) -> None:
    global _bank
    _bank = bank


def webhook_signature(body: bytes) -> str:
    return hmac.new(get_settings().bank_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
