"""Платежи этапа: резерв, выплата по акту, обработка ответов банка.

Один платёж на резерв этапа проходит статусы ТЗ: pending_funding → held → release_requested →
released (или failed с повтором). Повторная выплата по тому же акту невозможна: выплата идёт
только из статуса held под блокировкой строки, а act_id у платежа уникален.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.config import get_settings
from app.core.problems import Problem
from app.domain.statuses import ensure_transition
from app.models import Act, Dispute, Organization, Payment, RepairObject, Stage
from app.models.enums import (
    ActStatus,
    DisputeStatus,
    ObjectStatus,
    OrgRole,
    PaymentStatus,
    StageStatus,
)
from app.modules.notifications import service as notes
from app.modules.payments.bank import BankEvent, Recipient, get_bank


def _set(
    db: AsyncSession,
    entity: str,
    row: Any,
    target: Any,
    action: str,
    actor: uuid.UUID | None,
    **extra: Any,
) -> None:
    ensure_transition(entity, row.status, target)
    old = row.status
    row.status = target
    audit(
        db,
        entity_type=entity,
        entity_id=row.id,
        action=action,
        actor_id=actor,
        is_system=actor is None,
        old={"status": old},
        new={"status": target, **extra},
    )


async def apply_event(
    db: AsyncSession, event: BankEvent, actor: uuid.UUID | None = None
) -> Payment:
    """Применяет итог операции банка. Повтор того же события ничего не меняет."""
    payment = await db.get(Payment, event.payment_id, with_for_update=True)
    if payment is None:
        raise Problem(404, "not-found", "Платёж не найден")
    stage = (
        await db.get(Stage, payment.stage_id, with_for_update=True) if payment.stage_id else None
    )
    payload = dict(payment.bank_payload or {})
    if any(e.get("id") == event.event_id for e in payload.get("events", [])):
        return payment  # то же уведомление пришло повторно
    payload.setdefault("events", []).append({"id": event.event_id, "status": event.status})
    if event.store:
        payload["bank"] = {**payload.get("bank", {}), **event.store}

    if event.status == "held" and payment.status == PaymentStatus.pending_funding:
        _set(db, "payment", payment, PaymentStatus.held, "payment.held", actor)
        if stage and stage.status == StageStatus.funding:
            _set(
                db, "stage", stage, StageStatus.ready, "stage.funded", actor, payment_id=payment.id
            )
        obj = await db.get(RepairObject, payment.object_id, with_for_update=True)
        if stage and obj and obj.status == ObjectStatus.contracting:
            _set(db, "object", obj, ObjectStatus.active, "object.activated", actor)
        if stage and obj:
            bosses = await notes.org_member_ids(
                db, obj.contractor_org_id, [OrgRole.contractor_admin]
            )
            notes.notify(db, bosses, "hold_paid", stage=stage.title, address=obj.address_text)
        if payment.purchase_order_id:
            managers = await notes.platform_staff_ids(
                db, [OrgRole.procurement_manager, OrgRole.platform_admin]
            )
            notes.notify(
                db,
                managers,
                "order_paid",
                amount=notes.rub(payment.amount_kop),
                url=notes.url("/procurement"),
            )
    elif event.status == "failed" and payment.status in (
        PaymentStatus.pending_funding,
        PaymentStatus.release_requested,
    ):
        _set(
            db,
            "payment",
            payment,
            PaymentStatus.failed,
            "payment.failed",
            actor,
            reason=event.payload.get("reason"),
        )
    elif event.status == "released" and payment.status == PaymentStatus.release_requested:
        _set(
            db,
            "payment",
            payment,
            PaymentStatus.released,
            "payment.released",
            actor,
            bank_op_id=event.bank_op_id,
        )
        act = await db.get(Act, payment.act_id, with_for_update=True) if payment.act_id else None
        if act and act.status == ActStatus.signed_customer:
            _set(db, "act", act, ActStatus.released, "act.released", actor)
        if stage and stage.status == StageStatus.accepted:
            _set(db, "stage", stage, StageStatus.paid, "stage.paid", actor)
        if stage:
            obj = await db.get(RepairObject, payment.object_id)
            assert obj is not None
            bosses = await notes.org_member_ids(
                db, obj.contractor_org_id, [OrgRole.contractor_admin]
            )
            notes.notify(
                db,
                [*bosses, obj.customer_id],
                "payment_released",
                stage=stage.title,
                address=obj.address_text,
            )
    elif event.status == "frozen" and payment.status == PaymentStatus.held:
        _set(db, "payment", payment, PaymentStatus.frozen, "payment.frozen", actor)
    elif event.status == "unfrozen" and payment.status == PaymentStatus.frozen:
        _set(db, "payment", payment, PaymentStatus.held, "payment.unfrozen", actor)
    elif event.status == "refunded" and payment.status in (
        PaymentStatus.frozen,
        PaymentStatus.held,
    ):
        _set(db, "payment", payment, PaymentStatus.refunded, "payment.refunded", actor)
    payment.bank_payload = payload
    return payment


async def active_hold(
    db: AsyncSession, stage_id: uuid.UUID, *, lock: bool = False
) -> Payment | None:
    stmt = (
        select(Payment)
        .where(
            Payment.stage_id == stage_id,
            Payment.status.notin_([PaymentStatus.failed, PaymentStatus.refunded]),
        )
        .order_by(Payment.created_at.desc())
        .limit(1)
    )
    if lock:
        stmt = stmt.with_for_update()
    return (await db.execute(stmt)).scalar_one_or_none()


async def open_dispute(db: AsyncSession, stage_id: uuid.UUID) -> Dispute | None:
    return (
        await db.execute(
            select(Dispute)
            .where(
                Dispute.stage_id == stage_id,
                Dispute.status.in_([DisputeStatus.open, DisputeStatus.in_review]),
            )
            .limit(1)
        )
    ).scalar_one_or_none()


async def run(db: AsyncSession, event: BankEvent | None, actor: uuid.UUID | None) -> None:
    """Банк ответил сразу (имитация) — применяем; иначе ответ придёт вебхуком."""
    if event is not None:
        await apply_event(db, event, actor)


async def request_release(db: AsyncSession, act: Act, actor: uuid.UUID | None) -> Payment:
    """Выплата по подписанному обеими сторонами акту."""
    if act.status != ActStatus.signed_customer:
        raise Problem(409, "act-not-signed", "Акт ещё не подписан обеими сторонами")
    if await open_dispute(db, act.stage_id) is not None:
        raise Problem(409, "dispute-open", "По этапу открыт спор, выплата заморожена")
    payment = (
        await db.execute(
            select(Payment)
            .where(Payment.stage_id == act.stage_id)
            .where(Payment.status.in_([PaymentStatus.held, PaymentStatus.failed]))
            .order_by(Payment.created_at.desc())
            .limit(1)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if payment is None or (payment.act_id not in (None, act.id)):
        raise Problem(409, "no-hold", "Нет зарезервированных денег для выплаты по этому акту")
    if payment.status == PaymentStatus.failed and payment.act_id is None:
        raise Problem(409, "no-hold", "Резерв не состоялся")
    breakdown = payment.split.get("breakdown", {})
    obj = await db.get(RepairObject, act.object_id)
    assert obj is not None and obj.contractor_org_id is not None
    contractor = await db.get(Organization, obj.contractor_org_id)
    assert contractor is not None
    recipients = [
        Recipient(
            "contractor",
            contractor.inn,
            act.work_kop + act.material_kop,
            contractor.bank_partner_id,
        ),
    ]
    fee = int(breakdown.get("platform_fee_kop", 0))
    if fee:
        recipients.append(Recipient("platform", get_settings().platform_inn, fee))
    # Сумма выплат равна резерву: ни больше, ни меньше (ТЗ 7.2).
    if sum(r.amount_kop for r in recipients) != payment.amount_kop:
        raise Problem(
            409,
            "split-mismatch",
            "Сумма акта не совпадает с резервом",
            "Обратитесь к администратору.",
        )
    payment.act_id = act.id
    payment.split = {**payment.split, "recipients": [r.public() for r in recipients]}
    _set(
        db,
        "payment",
        payment,
        PaymentStatus.release_requested,
        "payment.release_requested",
        actor,
        act_id=act.id,
    )
    await db.flush()
    await run(db, await get_bank().release(payment, recipients), actor)
    return payment
