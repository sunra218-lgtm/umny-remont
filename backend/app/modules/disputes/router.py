"""Споры по этапу (ТЗ 2.3, разделы 3 и 5).

Спор открывает заказчик или руководитель подрядчика. Этап и его резерв сразу замораживаются:
работы по этапу не принимаются, выплата невозможна. Разбирает спор администратор платформы и
выбирает исход: продолжить работы (заморозка снимается, этап возвращается туда, где был) или
вернуть деньги заказчику (резерв возвращается, этап снова ждёт резерва).
"""

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.access import load_object
from app.core.audit import audit
from app.core.deps import Auth, DbSession, client_ip
from app.core.problems import Problem
from app.core.security import utcnow
from app.domain.statuses import ensure_transition
from app.models import Dispute, Payment, RepairObject, Stage, User
from app.models.enums import DisputeStatus, ObjectStatus, OrgRole, PaymentStatus, StageStatus
from app.modules.notifications import service as notes
from app.modules.payments import service as payments
from app.modules.payments.bank import get_bank

router = APIRouter(tags=["disputes"])

OPEN = (DisputeStatus.open, DisputeStatus.in_review)
# Спор можно открыть, пока по этапу не ушли деньги и он не на паузе.
DISPUTABLE = {
    StageStatus.funding,
    StageStatus.ready,
    StageStatus.in_progress,
    StageStatus.inspection,
    StageStatus.acceptance,
    StageStatus.accepted,
}
OUTCOME_TITLES = {"continue": "работы продолжаются", "refund": "деньги возвращены заказчику"}


class DisputeIn(BaseModel):
    stage_id: uuid.UUID
    reason: str = Field(min_length=10, max_length=4000)


class ResolveIn(BaseModel):
    outcome: Literal["continue", "refund"]
    resolution: str = Field(min_length=5, max_length=4000)


class DisputeOut(BaseModel):
    id: uuid.UUID
    object_id: uuid.UUID
    stage_id: uuid.UUID | None
    stage_title: str | None
    address: str | None
    opened_by: uuid.UUID
    opened_by_name: str | None
    reason: str
    status: DisputeStatus
    outcome: str | None
    resolution: str | None
    stage_status_before: StageStatus | None
    payment_status: PaymentStatus | None
    created_at: datetime
    resolved_at: datetime | None


async def _out(db: DbSession, d: Dispute) -> DisputeOut:
    stage = await db.get(Stage, d.stage_id) if d.stage_id else None
    obj = await db.get(RepairObject, d.object_id)
    author = await db.get(User, d.opened_by)
    payment = await db.get(Payment, d.frozen_payment_id) if d.frozen_payment_id else None
    return DisputeOut(
        id=d.id,
        object_id=d.object_id,
        stage_id=d.stage_id,
        stage_title=stage.title if stage else None,
        address=obj.address_text if obj else None,
        opened_by=d.opened_by,
        opened_by_name=(author.full_name or author.phone) if author else None,
        reason=d.reason,
        status=d.status,
        outcome=d.outcome,
        resolution=d.resolution,
        stage_status_before=d.stage_status_before,
        payment_status=payment.status if payment else None,
        created_at=d.created_at,
        resolved_at=d.resolved_at,
    )


async def _parties(db: DbSession, obj: RepairObject) -> list[uuid.UUID]:
    bosses = await notes.org_member_ids(db, obj.contractor_org_id, [OrgRole.contractor_admin])
    return [obj.customer_id, *bosses]


def _set_status(db: DbSession, entity: str, row, target, action: str, actor: uuid.UUID, ip):  # type: ignore[no-untyped-def]
    ensure_transition(entity, row.status, target)
    old = row.status
    row.status = target
    audit(
        db,
        entity_type=entity,
        entity_id=row.id,
        action=action,
        actor_id=actor,
        ip=ip,
        old={"status": old},
        new={"status": target},
    )


@router.post(
    "/objects/{object_id}/disputes",
    response_model=DisputeOut,
    status_code=201,
    summary="Открыть спор по этапу",
)
async def open_dispute(
    object_id: uuid.UUID, body: DisputeIn, current: Auth, request: Request, db: DbSession
) -> DisputeOut:
    access = await load_object(db, current, object_id, lock=True)
    if not (access.is_customer or OrgRole.contractor_admin in access.contractor_roles):
        raise Problem(403, "forbidden", "Спор открывает заказчик или руководитель подрядчика")
    stage = await db.get(Stage, body.stage_id, with_for_update=True)
    if stage is None or stage.object_id != object_id or stage.deleted_at is not None:
        raise Problem(404, "not-found", "Этап не найден")
    if await payments.open_dispute(db, stage.id) is not None:
        raise Problem(409, "dispute-open", "По этому этапу спор уже открыт")
    if stage.status not in DISPUTABLE:
        raise Problem(
            409,
            "stage-not-disputable",
            "По этому этапу спор открыть нельзя",
            "Спор возможен, пока деньги этапа не выплачены.",
        )
    hold = await payments.active_hold(db, stage.id, lock=True)
    if hold is not None and hold.status == PaymentStatus.release_requested:
        raise Problem(409, "release-in-progress", "Выплата по этапу уже отправлена в банк")
    ip = client_ip(request)
    dispute = Dispute(
        object_id=object_id,
        stage_id=stage.id,
        opened_by=current.id,
        reason=body.reason,
        status=DisputeStatus.open,
        stage_status_before=stage.status,
        frozen_payment_id=hold.id if hold and hold.status == PaymentStatus.held else None,
    )
    db.add(dispute)
    await db.flush()
    _set_status(db, "stage", stage, StageStatus.frozen, "stage.frozen", current.id, ip)
    if dispute.frozen_payment_id and hold is not None:
        await payments.run(db, await get_bank().freeze(hold), current.id)
    obj = access.obj
    if obj.status == ObjectStatus.active:
        _set_status(db, "object", obj, ObjectStatus.dispute, "object.dispute", current.id, ip)
    audit(
        db,
        entity_type="dispute",
        entity_id=dispute.id,
        action="dispute.opened",
        actor_id=current.id,
        ip=ip,
        new={"stage_id": stage.id, "reason": body.reason, "payment_id": dispute.frozen_payment_id},
    )
    admins = await notes.platform_staff_ids(db, [OrgRole.platform_admin])
    notes.notify(
        db,
        [*await _parties(db, obj), *admins],
        "dispute_opened",
        stage=stage.title,
        url=notes.url(f"/stages/{stage.id}"),
    )
    await db.commit()
    return await _out(db, dispute)


@router.get(
    "/objects/{object_id}/disputes", response_model=list[DisputeOut], summary="Споры объекта"
)
async def object_disputes(object_id: uuid.UUID, current: Auth, db: DbSession) -> list[DisputeOut]:
    await load_object(db, current, object_id)
    rows = await db.execute(
        select(Dispute).where(Dispute.object_id == object_id).order_by(Dispute.created_at.desc())
    )
    return [await _out(db, d) for d in rows.scalars()]


@router.get("/disputes", response_model=list[DisputeOut], summary="Все споры (администратор)")
async def all_disputes(
    current: Auth, db: DbSession, open_only: bool = Query(default=True)
) -> list[DisputeOut]:
    if not current.is_platform_admin:
        raise Problem(403, "forbidden", "Споры разбирает администратор платформы")
    stmt = select(Dispute).order_by(Dispute.created_at.desc()).limit(200)
    if open_only:
        stmt = stmt.where(Dispute.status.in_(OPEN))
    return [await _out(db, d) for d in (await db.execute(stmt)).scalars()]


async def _admin_dispute(db: DbSession, current: Auth, dispute_id: uuid.UUID) -> Dispute:
    if not current.is_platform_admin:
        raise Problem(403, "forbidden", "Споры разбирает администратор платформы")
    dispute = await db.get(Dispute, dispute_id, with_for_update=True)
    if dispute is None:
        raise Problem(404, "not-found", "Спор не найден")
    if dispute.status not in OPEN:
        raise Problem(409, "dispute-closed", "Спор уже закрыт")
    return dispute


@router.post("/disputes/{dispute_id}/review", response_model=DisputeOut, summary="Взять в работу")
async def take_dispute(
    dispute_id: uuid.UUID, current: Auth, request: Request, db: DbSession
) -> DisputeOut:
    dispute = await _admin_dispute(db, current, dispute_id)
    if dispute.status == DisputeStatus.open:
        dispute.status = DisputeStatus.in_review
        audit(
            db,
            entity_type="dispute",
            entity_id=dispute.id,
            action="dispute.in_review",
            actor_id=current.id,
            ip=client_ip(request),
            old={"status": DisputeStatus.open},
            new={"status": DisputeStatus.in_review},
        )
        await db.commit()
    return await _out(db, dispute)


@router.post("/disputes/{dispute_id}/resolve", response_model=DisputeOut, summary="Решить спор")
async def resolve_dispute(
    dispute_id: uuid.UUID, body: ResolveIn, current: Auth, request: Request, db: DbSession
) -> DisputeOut:
    dispute = await _admin_dispute(db, current, dispute_id)
    ip = client_ip(request)
    obj = await db.get(RepairObject, dispute.object_id, with_for_update=True)
    stage = (
        await db.get(Stage, dispute.stage_id, with_for_update=True) if dispute.stage_id else None
    )
    assert obj is not None and stage is not None
    payment = (
        await db.get(Payment, dispute.frozen_payment_id, with_for_update=True)
        if dispute.frozen_payment_id
        else None
    )
    bank = get_bank()
    if body.outcome == "continue":
        if payment is not None and payment.status == PaymentStatus.frozen:
            await payments.run(db, await bank.unfreeze(payment), current.id)
        target = dispute.stage_status_before or StageStatus.ready
    else:
        if payment is not None and payment.status in (PaymentStatus.frozen, PaymentStatus.held):
            await payments.run(db, await bank.refund(payment, payment.amount_kop), current.id)
        # Пока резерва нет, этап снова ждёт денег; без резерва — планируется заново.
        target = StageStatus.funding
        # Неподписанные акты по этапу теряют смысл: деньги вернулись.
        from app.modules.acts.router import cancel_open_acts

        await cancel_open_acts(db, stage.id, current.id, "Спор решён возвратом денег")
    if stage.status == StageStatus.frozen:
        _set_status(db, "stage", stage, target, "stage.unfrozen", current.id, ip)
    dispute.status = DisputeStatus.resolved
    dispute.outcome = body.outcome
    dispute.resolution = body.resolution
    dispute.resolved_at = utcnow()
    await db.flush()
    others = await db.execute(
        select(Dispute.id).where(
            Dispute.object_id == obj.id, Dispute.status.in_(OPEN), Dispute.id != dispute.id
        )
    )
    if obj.status == ObjectStatus.dispute and others.first() is None:
        _set_status(db, "object", obj, ObjectStatus.active, "object.dispute_closed", current.id, ip)
    audit(
        db,
        entity_type="dispute",
        entity_id=dispute.id,
        action="dispute.resolved",
        actor_id=current.id,
        ip=ip,
        new={"outcome": body.outcome, "resolution": body.resolution, "stage_status": target},
    )
    notes.notify(
        db,
        await _parties(db, obj),
        "dispute_resolved",
        stage=stage.title,
        result=OUTCOME_TITLES[body.outcome],
        url=notes.url(f"/stages/{stage.id}"),
    )
    await db.commit()
    return await _out(db, dispute)
