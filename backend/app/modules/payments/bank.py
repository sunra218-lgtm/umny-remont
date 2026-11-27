"""Банк безопасной сделки (ТЗ 2.3, раздел 7.1–7.2).

Платформа работает с банком через этот интерфейс. Сейчас подключена имитация `FakeBank`:
резерв «оплачивается» на тестовой странице сайта, выплата проходит сразу. Настоящий банк
(выбран Т-Банк) добавляется ещё одним классом с теми же
методами; остальной код не меняется. Итоги операций банк присылает вебхуком `/webhooks/bank`.
"""

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol

from app.core.config import get_settings
from app.core.security import utcnow


@dataclass
class Recipient:
    role: str  # contractor | supplier | platform
    inn: str
    amount_kop: int


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


class BankGateway(Protocol):
    name: str

    async def create_hold(self, payment_id: uuid.UUID, amount_kop: int, purpose: str) -> HoldResult:
        """Счёт на резерв: заказчик оплачивает его по ссылке (СБП/карта)."""
        ...

    async def release(self, payment_id: uuid.UUID, recipients: list[Recipient]) -> BankEvent | None:
        """Выплата из резерва. Настоящий банк ответит вебхуком; имитация — сразу."""
        ...

    async def freeze(self, payment_id: uuid.UUID) -> BankEvent | None:
        """Заморозка резерва на время спора: деньги не уходят ни одной стороне."""
        ...

    async def unfreeze(self, payment_id: uuid.UUID) -> BankEvent | None:
        """Снятие заморозки: резерв снова доступен для выплаты по акту."""
        ...

    async def refund(self, payment_id: uuid.UUID, amount_kop: int) -> BankEvent | None:
        """Возврат резерва заказчику."""
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

    async def release(self, payment_id: uuid.UUID, recipients: list[Recipient]) -> BankEvent | None:
        return BankEvent(
            event_id=f"fake-release-{payment_id}",
            payment_id=payment_id,
            status="released",
            bank_op_id=f"fake-release-{payment_id}",
            payload={"recipients": [r.__dict__ for r in recipients]},
        )

    async def freeze(self, payment_id: uuid.UUID) -> BankEvent | None:
        return BankEvent(f"fake-freeze-{payment_id}-{utcnow().timestamp()}", payment_id, "frozen")

    async def unfreeze(self, payment_id: uuid.UUID) -> BankEvent | None:
        return BankEvent(
            f"fake-unfreeze-{payment_id}-{utcnow().timestamp()}", payment_id, "unfrozen"
        )

    async def refund(self, payment_id: uuid.UUID, amount_kop: int) -> BankEvent | None:
        return BankEvent(
            f"fake-refund-{payment_id}", payment_id, "refunded", payload={"amount_kop": amount_kop}
        )


_bank: BankGateway | None = None


def get_bank() -> BankGateway:
    global _bank
    if _bank is None:
        _bank = FakeBank()
    return _bank


def set_bank(bank: BankGateway | None) -> None:
    global _bank
    _bank = bank


def webhook_signature(body: bytes) -> str:
    return hmac.new(get_settings().bank_webhook_secret.encode(), body, hashlib.sha256).hexdigest()


def parse_webhook(body: bytes, signature: str | None) -> BankEvent | None:
    if not signature or not hmac.compare_digest(signature, webhook_signature(body)):
        return None
    data = json.loads(body)
    return BankEvent(
        event_id=str(data["event_id"]),
        payment_id=uuid.UUID(str(data["payment_id"])),
        status=str(data["status"]),
        bank_op_id=data.get("bank_op_id"),
        payload=data,
    )
