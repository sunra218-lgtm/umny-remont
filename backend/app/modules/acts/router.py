"""Акт сдачи-приёмки этапа: формирует подрядчик, подписывают подрядчик и заказчик кодом из СМС
(ПЭП, 63-ФЗ). После подписи заказчика деньги этапа уходят подрядчику (ТЗ 2.3, разделы 5 и 7)."""

import hashlib
import json
import uuid
from datetime import datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.core.access import ObjectAccess, load_object
from app.core.audit import audit
from app.core.deps import Auth, CurrentUser, DbSession, client_ip
from app.core.problems import Problem
from app.core.redis import get_redis
from app.domain.statuses import ensure_transition
from app.models import Act, Inspection, Organization, Payment, Signature, Stage
from app.models.enums import (
    ActStatus,
    InspectionResult,
    OrgRole,
    PaymentStatus,
    SignedDocumentType,
    StageStatus,
)
from app.modules.notifications import service as notes
from app.modules.payments import service as payments
from app.modules.signing.service import confirm_signature, request_signature

router = APIRouter(tags=["acts"])

OPEN = (ActStatus.draft, ActStatus.sent, ActStatus.signed_contractor, ActStatus.signed_customer)


class ActOut(BaseModel):
    id: uuid.UUID
    stage_id: uuid.UUID
    number: str
    work_kop: int
    material_kop: int
    status: ActStatus
    reject_reason: str | None
    created_at: datetime
    contractor_signed_at: datetime | None
    customer_signed_at: datetime | None
    payment_status: PaymentStatus | None
    can_sign: bool
    can_reject: bool


class SignRequestOut(BaseModel):
    signature_id: uuid.UUID
    expires_in: int
    debug_code: str | None = None


class SignConfirmIn(BaseModel):
    signature_id: uuid.UUID
    code: str = Field(pattern=r"^\d{6}$")


class RejectIn(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)


def _is_contractor_signer(access: ObjectAccess) -> bool:
    return OrgRole.contractor_admin in access.contractor_roles


def _signer_role(act: Act, access: ObjectAccess) -> str | None:
    if act.status == ActStatus.sent and _is_contractor_signer(access):
        return "contractor"
    if act.status == ActStatus.signed_contractor and access.is_customer:
        return "customer"
    return None


async def _signed_at(db: DbSession, act: Act) -> list[datetime]:
    rows = await db.execute(
        select(Signature.signed_at)
        .where(
            Signature.document_type == SignedDocumentType.act,
            Signature.document_id == act.id,
            Signature.signed_at.is_not(None),
        )
        .order_by(Signature.signed_at)
    )
    return [r for r in rows.scalars() if r is not None]


async def act_out(db: DbSession, act: Act, access: ObjectAccess) -> ActOut:
    signed = await _signed_at(db, act)
    payment_status = await db.scalar(
        select(Payment.status).where(Payment.act_id == act.id).limit(1)
    )
    return ActOut(
        id=act.id,
        stage_id=act.stage_id,
        number=act.number,
        work_kop=act.work_kop,
        material_kop=act.material_kop,
        status=act.status,
        reject_reason=act.reject_reason,
        created_at=act.created_at,
        contractor_signed_at=signed[0] if len(signed) > 0 else None,
        customer_signed_at=signed[1] if len(signed) > 1 else None,
        payment_status=payment_status,
        can_sign=_signer_role(act, access) is not None,
        can_reject=access.is_customer
        and act.status in (ActStatus.sent, ActStatus.signed_contractor),
    )


async def document_hash(db: DbSession, act: Act, access: ObjectAccess) -> str:
    """Подпись относится ровно к этому акту: номер, этап, суммы, стороны."""
    stage = await db.get(Stage, act.stage_id)
    org = await db.get(Organization, access.obj.contractor_org_id)
    payload = {
        "act_id": str(act.id),
        "number": act.number,
        "object_id": str(act.object_id),
        "address": access.obj.address_text,
        "stage": stage.title if stage else None,
        "work_kop": act.work_kop,
        "material_kop": act.material_kop,
        "inspection_id": str(act.inspection_id) if act.inspection_id else None,
        "contractor_inn": org.inn if org else None,
        "customer_id": str(access.obj.customer_id),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


async def _load_act(
    db: DbSession, current: CurrentUser, act_id: uuid.UUID
) -> tuple[Act, ObjectAccess]:
    act = await db.get(Act, act_id, with_for_update=True)
    if act is None:
        raise Problem(404, "not-found", "Акт не найден")
    access = await load_object(db, current, act.object_id)
    stage_status = await db.scalar(select(Stage.status).where(Stage.id == act.stage_id))
    if stage_status == StageStatus.frozen:
        raise Problem(409, "dispute-open", "По этапу открыт спор, акт на паузе")
    return act, access


async def cancel_open_acts(
    db: DbSession, stage_id: uuid.UUID, actor: uuid.UUID, reason: str
) -> None:
    """Акты, которые ещё не подписал заказчик, отменяются (например, после возврата денег)."""
    rows = await db.execute(
        select(Act)
        .where(
            Act.stage_id == stage_id,
            Act.status.in_([ActStatus.sent, ActStatus.signed_contractor]),
        )
        .with_for_update()
    )
    for act in rows.scalars():
        old = act.status
        ensure_transition("act", act.status, ActStatus.rejected)
        act.status = ActStatus.rejected
        act.reject_reason = reason
        audit(
            db,
            entity_type="act",
            entity_id=act.id,
            action="act.cancelled",
            actor_id=actor,
            old={"status": old},
            new={"status": act.status, "reason": reason},
        )


@router.post(
    "/stages/{stage_id}/acts",
    response_model=ActOut,
    status_code=201,
    summary="Сформировать акт этапа",
)
async def create_act(stage_id: uuid.UUID, current: Auth, request: Request, db: DbSession) -> ActOut:
    stage = await db.get(Stage, stage_id, with_for_update=True)
    if stage is None or stage.deleted_at is not None:
        raise Problem(404, "not-found", "Этап не найден")
    access = await load_object(db, current, stage.object_id, lock=True)
    if not (_is_contractor_signer(access) or access.is_admin):
        raise Problem(403, "forbidden", "Акт формирует руководитель подрядчика")
    if stage.status != StageStatus.acceptance:
        raise Problem(409, "stage-not-ready", "Этап ещё не сдан на приёмку")
    if await db.scalar(select(Act.id).where(Act.stage_id == stage.id, Act.status.in_(OPEN))):
        raise Problem(409, "act-exists", "По этапу уже есть акт")
    hold = await payments.active_hold(db, stage.id)
    if hold is None or hold.status != PaymentStatus.held:
        raise Problem(409, "no-hold", "Деньги на этап не зарезервированы")
    inspection_id = None
    if stage.is_stop_point:
        inspection_id = await db.scalar(
            select(Inspection.id)
            .where(Inspection.stage_id == stage.id, Inspection.result == InspectionResult.passed)
            .order_by(Inspection.signed_at.desc())
            .limit(1)
        )
        if inspection_id is None:
            raise Problem(409, "stop-point-not-passed", "Стоп-точка не пройдена")
    n = await db.scalar(
        select(func.count()).select_from(Act).where(Act.object_id == stage.object_id)
    )
    act = Act(
        object_id=stage.object_id,
        stage_id=stage.id,
        number=f"{stage.seq}-{(n or 0) + 1}",
        work_kop=stage.work_budget_kop,
        material_kop=stage.material_budget_kop,
        inspection_id=inspection_id,
        status=ActStatus.draft,
    )
    db.add(act)
    await db.flush()
    ensure_transition("act", act.status, ActStatus.sent)
    act.status = ActStatus.sent
    audit(
        db,
        entity_type="act",
        entity_id=act.id,
        action="act.created",
        actor_id=current.id,
        ip=client_ip(request),
        new={
            "number": act.number,
            "work_kop": act.work_kop,
            "material_kop": act.material_kop,
            "status": act.status,
        },
    )
    await db.commit()
    return await act_out(db, act, access)


@router.get("/stages/{stage_id}/acts", response_model=list[ActOut], summary="Акты этапа")
async def stage_acts(stage_id: uuid.UUID, current: Auth, db: DbSession) -> list[ActOut]:
    stage = await db.get(Stage, stage_id)
    if stage is None:
        raise Problem(404, "not-found", "Этап не найден")
    access = await load_object(db, current, stage.object_id)
    rows = await db.execute(
        select(Act).where(Act.stage_id == stage_id).order_by(Act.created_at.desc())
    )
    return [await act_out(db, a, access) for a in rows.scalars()]


@router.post(
    "/acts/{act_id}/sign/request",
    response_model=SignRequestOut,
    summary="Получить СМС-код для подписи акта",
)
async def sign_request(
    act_id: uuid.UUID, current: Auth, request: Request, db: DbSession
) -> SignRequestOut:
    act, access = await _load_act(db, current, act_id)
    role = _signer_role(act, access)
    if role is None:
        raise Problem(403, "forbidden", "Сейчас акт подписывает другая сторона")
    total = (act.work_kop + act.material_kop) / 100
    result = await request_signature(
        db,
        get_redis(),
        user=current.user,
        document_type=SignedDocumentType.act,
        document_id=act.id,
        document_hash=await document_hash(db, act, access),
        sms_text=f"Код {{code}} — подпись акта № {act.number} на {total:,.2f} руб.".replace(
            ",", " "
        ),
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    return SignRequestOut(
        signature_id=result.signature.id, expires_in=result.expires_in, debug_code=result.debug_code
    )


@router.post("/acts/{act_id}/sign/confirm", response_model=ActOut, summary="Подписать акт кодом")
async def sign_confirm(
    act_id: uuid.UUID, body: SignConfirmIn, current: Auth, request: Request, db: DbSession
) -> ActOut:
    act, access = await _load_act(db, current, act_id)
    role = _signer_role(act, access)
    if role is None:
        raise Problem(403, "forbidden", "Сейчас акт подписывает другая сторона")
    signature = await db.get(Signature, body.signature_id, with_for_update=True)
    if (
        signature is None
        or signature.document_id != act.id
        or signature.document_type != SignedDocumentType.act
    ):
        raise Problem(404, "not-found", "Запрос подписи не найден")
    await confirm_signature(
        get_redis(), signature, current.user, body.code, await document_hash(db, act, access)
    )
    target = ActStatus.signed_contractor if role == "contractor" else ActStatus.signed_customer
    ensure_transition("act", act.status, target)
    old = act.status
    act.status = target
    audit(
        db,
        entity_type="act",
        entity_id=act.id,
        action=f"act.signed_{role}",
        actor_id=current.id,
        ip=client_ip(request),
        old={"status": old},
        new={
            "status": target,
            "signature_id": signature.id,
            "document_hash": signature.document_hash,
        },
    )
    if role == "contractor":
        stage = await db.get(Stage, act.stage_id)
        notes.notify(
            db,
            [access.obj.customer_id],
            "act_to_sign",
            stage=stage.title if stage else "",
            url=notes.url(f"/stages/{act.stage_id}"),
        )
    if role == "customer":
        stage = await db.get(Stage, act.stage_id, with_for_update=True)
        assert stage is not None
        ensure_transition("stage", stage.status, StageStatus.accepted)
        stage.status = StageStatus.accepted
        audit(
            db,
            entity_type="stage",
            entity_id=stage.id,
            action="stage.accepted",
            actor_id=current.id,
            ip=client_ip(request),
            new={"act_id": act.id},
        )
        await payments.request_release(db, act, current.id)
    await db.commit()
    return await act_out(db, act, access)


@router.post("/acts/{act_id}/reject", response_model=ActOut, summary="Отклонить акт")
async def reject(
    act_id: uuid.UUID, body: RejectIn, current: Auth, request: Request, db: DbSession
) -> ActOut:
    act, access = await _load_act(db, current, act_id)
    if not access.is_customer:
        raise Problem(403, "forbidden", "Акт отклоняет заказчик")
    ensure_transition("act", act.status, ActStatus.rejected)
    act.status = ActStatus.rejected
    act.reject_reason = body.reason.strip()
    stage = await db.get(Stage, act.stage_id, with_for_update=True)
    assert stage is not None
    ensure_transition("stage", stage.status, StageStatus.in_progress)
    stage.status = StageStatus.in_progress
    audit(
        db,
        entity_type="act",
        entity_id=act.id,
        action="act.rejected",
        actor_id=current.id,
        ip=client_ip(request),
        new={"reason": act.reject_reason},
    )
    await db.commit()
    return await act_out(db, act, access)
