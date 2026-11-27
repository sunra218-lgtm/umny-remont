"""Резерв денег на этап, тестовая оплата (имитация банка), вебхук банка, повтор выплаты."""

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import select

from app.core.access import load_object
from app.core.audit import audit
from app.core.config import get_settings
from app.core.deps import Auth, DbSession, client_ip
from app.core.problems import Problem
from app.domain.statuses import ensure_transition
from app.models import Act, Payment, Stage
from app.models.enums import ObjectStatus, PaymentStatus, PaymentType, StageStatus
from app.modules.payments import service
from app.modules.payments.bank import BankEvent, get_bank, parse_webhook

router = APIRouter(tags=["payments"])


class PaymentOut(BaseModel):
    id: uuid.UUID
    stage_id: uuid.UUID | None
    object_id: uuid.UUID
    act_id: uuid.UUID | None
    status: PaymentStatus
    amount_kop: int
    breakdown: dict[str, int]
    recipients: list[dict[str, Any]] = []
    pay_url: str | None
    expires_at: datetime | None
    created_at: datetime
    stage_title: str | None = None
    address: str | None = None


def payment_out(p: Payment, stage: Stage | None = None, address: str | None = None) -> PaymentOut:
    hold = p.split.get("hold", {})
    return PaymentOut(
        id=p.id,
        stage_id=p.stage_id,
        object_id=p.object_id,
        act_id=p.act_id,
        status=p.status,
        amount_kop=p.amount_kop,
        breakdown=p.split.get("breakdown", {}),
        recipients=p.split.get("recipients", []),
        pay_url=hold.get("pay_url") if p.status == PaymentStatus.pending_funding else None,
        expires_at=hold.get("expires_at"),
        created_at=p.created_at,
        stage_title=stage.title if stage else None,
        address=address,
    )


async def _stage_access(db: DbSession, current: Auth, stage_id: uuid.UUID, lock: bool = False):  # type: ignore[no-untyped-def]
    stage = await db.get(Stage, stage_id, with_for_update=lock)
    if stage is None or stage.deleted_at is not None:
        raise Problem(404, "not-found", "Этап не найден")
    return stage, await load_object(db, current, stage.object_id, lock=lock)


@router.post(
    "/stages/{stage_id}/payments/hold",
    response_model=PaymentOut,
    status_code=201,
    summary="Зарезервировать деньги на этап",
)
async def hold(stage_id: uuid.UUID, current: Auth, request: Request, db: DbSession) -> PaymentOut:
    stage, access = await _stage_access(db, current, stage_id, lock=True)
    if not access.is_customer:
        raise Problem(403, "forbidden", "Деньги резервирует заказчик")
    obj = access.obj
    if obj.status not in (ObjectStatus.contracting, ObjectStatus.active):
        raise Problem(409, "object-not-contracted", "Сначала утвердите полную смету")
    if stage.status not in (StageStatus.planned, StageStatus.funding):
        raise Problem(409, "stage-funded", "Деньги на этот этап уже зарезервированы")
    existing = await service.active_hold(db, stage.id)
    if existing is not None:
        # Повторное нажатие отдаёт тот же счёт, второй резерв не создаётся.
        return payment_out(existing, stage, obj.address_text)
    work, material = stage.work_budget_kop, stage.material_budget_kop
    if work + material <= 0:
        raise Problem(409, "stage-no-budget", "У этапа нет суммы в смете")
    fee = (work + material) * get_settings().platform_fee_bp // 10_000
    breakdown = {"work_kop": work, "material_kop": material, "platform_fee_kop": fee}
    payment = Payment(
        object_id=obj.id,
        stage_id=stage.id,
        type=PaymentType.hold,
        idempotency_key=request.headers.get("idempotency-key") or str(uuid.uuid4()),
        amount_kop=work + material + fee,
        split={"breakdown": breakdown},
        status=PaymentStatus.created,
    )
    db.add(payment)
    await db.flush()
    result = await get_bank().create_hold(
        payment.id, payment.amount_kop, f"Резерв этапа «{stage.title}», {obj.address_text}"
    )
    payment.bank_op_id = result.bank_op_id
    payment.split = {
        **payment.split,
        "hold": {"pay_url": result.pay_url, "expires_at": result.expires_at.isoformat()},
    }
    ensure_transition("payment", payment.status, PaymentStatus.pending_funding)
    payment.status = PaymentStatus.pending_funding
    if stage.status == StageStatus.planned:
        ensure_transition("stage", stage.status, StageStatus.funding)
        stage.status = StageStatus.funding
    audit(
        db,
        entity_type="payment",
        entity_id=payment.id,
        action="payment.hold_created",
        actor_id=current.id,
        ip=client_ip(request),
        new={"amount_kop": payment.amount_kop, **breakdown, "stage_id": stage.id},
    )
    await db.commit()
    return payment_out(payment, stage, obj.address_text)


@router.get("/stages/{stage_id}/payments", response_model=list[PaymentOut], summary="Платежи этапа")
async def stage_payments(stage_id: uuid.UUID, current: Auth, db: DbSession) -> list[PaymentOut]:
    stage, access = await _stage_access(db, current, stage_id)
    rows = await db.execute(
        select(Payment).where(Payment.stage_id == stage.id).order_by(Payment.created_at.desc())
    )
    return [payment_out(p, stage, access.obj.address_text) for p in rows.scalars()]


@router.get("/payments/{payment_id}", response_model=PaymentOut, summary="Платёж")
async def get_payment(payment_id: uuid.UUID, current: Auth, db: DbSession) -> PaymentOut:
    payment = await db.get(Payment, payment_id)
    if payment is None:
        raise Problem(404, "not-found", "Платёж не найден")
    access = await load_object(db, current, payment.object_id)
    stage = await db.get(Stage, payment.stage_id) if payment.stage_id else None
    return payment_out(payment, stage, access.obj.address_text)


class FakePayIn(BaseModel):
    success: bool = True


@router.post(
    "/fake-bank/payments/{payment_id}/pay",
    response_model=PaymentOut,
    summary="Тестовая оплата (только имитация банка)",
)
async def fake_pay(
    payment_id: uuid.UUID, body: FakePayIn, current: Auth, db: DbSession
) -> PaymentOut:
    if get_bank().name != "fake":
        raise Problem(404, "not-found", "Не найдено")
    payment = await db.get(Payment, payment_id)
    if payment is None:
        raise Problem(404, "not-found", "Платёж не найден")
    access = await load_object(db, current, payment.object_id)
    if not access.is_customer:
        raise Problem(403, "forbidden", "Оплачивает заказчик")
    event = BankEvent(
        event_id=f"fake-pay-{payment.id}",
        payment_id=payment.id,
        status="held" if body.success else "failed",
        payload={"reason": None if body.success else "Оплата отменена"},
    )
    payment = await service.apply_event(db, event)
    await db.commit()
    stage = await db.get(Stage, payment.stage_id) if payment.stage_id else None
    return payment_out(payment, stage, access.obj.address_text)


@router.post("/webhooks/bank", summary="Уведомление банка об операции")
async def bank_webhook(request: Request, db: DbSession) -> dict[str, bool]:
    body = await request.body()
    event = parse_webhook(body, request.headers.get("x-bank-signature"))
    if event is None:
        raise Problem(401, "bad-signature", "Подпись уведомления не прошла")
    try:
        await service.apply_event(db, event)
    except Problem as exc:
        if exc.status == 404:
            return {"ok": False}
        raise
    await db.commit()
    return {"ok": True}


class ReleaseIn(BaseModel):
    act_id: uuid.UUID


@router.post(
    "/stages/{stage_id}/payments/release",
    response_model=PaymentOut,
    summary="Повторить выплату по акту (администратор)",
)
async def release(stage_id: uuid.UUID, body: ReleaseIn, current: Auth, db: DbSession) -> PaymentOut:
    """Выплата идёт сама после подписи заказчика. Здесь — повтор, если банк ответил ошибкой."""
    stage, access = await _stage_access(db, current, stage_id)
    if not access.is_admin:
        raise Problem(403, "forbidden", "Повтор выплаты запускает администратор")
    act = await db.get(Act, body.act_id, with_for_update=True)
    if act is None or act.stage_id != stage.id:
        raise Problem(404, "not-found", "Акт не найден")
    payment = await service.request_release(db, act, current.id)
    await db.commit()
    return payment_out(payment, stage, access.obj.address_text)
