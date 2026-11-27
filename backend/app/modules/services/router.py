"""Платные выезды: «Умная приёмка» и замер (ТЗ 2.3, раздел 1.2)."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.access import load_object
from app.core.audit import audit
from app.core.config import get_settings
from app.core.deps import Auth, CurrentUser, DbSession, client_ip
from app.core.problems import Problem
from app.core.security import utcnow
from app.domain.statuses import ensure_transition
from app.models import Organization, OrgMember, RepairObject, ServiceOrder, User
from app.models.enums import (
    AssignmentRole,
    ObjectStatus,
    OrgRole,
    OrgType,
    ServiceOrderStatus,
    ServiceOrderType,
)
from app.modules.objects.router import ensure_assignment
from app.modules.objects.schemas import PersonOut

router = APIRouter(tags=["service-orders"])

ACTIVE = {ServiceOrderStatus.created, ServiceOrderStatus.scheduled, ServiceOrderStatus.paid}


class ServiceOrderCreate(BaseModel):
    type: ServiceOrderType
    preferred_at: datetime | None = None
    comment: str | None = Field(default=None, max_length=2000)


class ServiceOrderSchedule(BaseModel):
    inspector_id: uuid.UUID
    slot_at: datetime
    price_kop: int | None = Field(default=None, ge=0, le=100_000_000)


class ServiceOrderComplete(BaseModel):
    result_notes: str = Field(min_length=3, max_length=10_000)


class ServiceOrderOut(BaseModel):
    id: uuid.UUID
    object_id: uuid.UUID
    type: ServiceOrderType
    status: ServiceOrderStatus
    price_kop: int
    slot_at: datetime | None
    paid_at: datetime | None
    completed_at: datetime | None
    customer_comment: str | None
    result_notes: str | None
    inspector: PersonOut | None
    created_at: datetime


def default_price(kind: ServiceOrderType) -> int:
    s = get_settings()
    return s.price_acceptance_kop if kind == ServiceOrderType.acceptance else s.price_survey_kop


async def _out(db: DbSession, order: ServiceOrder) -> ServiceOrderOut:
    inspector = await db.get(User, order.inspector_id) if order.inspector_id else None
    return ServiceOrderOut(
        id=order.id,
        object_id=order.object_id,
        type=order.type,
        status=order.status,
        price_kop=order.price_kop,
        slot_at=order.slot_at,
        paid_at=order.paid_at,
        completed_at=order.completed_at,
        customer_comment=order.customer_comment,
        result_notes=order.result_notes,
        inspector=PersonOut(id=inspector.id, phone=inspector.phone, full_name=inspector.full_name)
        if inspector
        else None,
        created_at=order.created_at,
    )


async def _load(
    db: DbSession, current: CurrentUser, order_id: uuid.UUID
) -> tuple[ServiceOrder, RepairObject, bool]:
    order = (
        await db.execute(select(ServiceOrder).where(ServiceOrder.id == order_id).with_for_update())
    ).scalar_one_or_none()
    if order is None:
        raise Problem(404, "not-found", "Выезд не найден")
    access = await load_object(db, current, order.object_id, lock=True)
    is_inspector = order.inspector_id == current.id
    return order, access.obj, is_inspector


def _set_status(order: ServiceOrder, target: ServiceOrderStatus) -> None:
    ensure_transition("service_order", order.status, target)
    order.status = target


@router.post(
    "/objects/{object_id}/service-orders",
    response_model=ServiceOrderOut,
    status_code=201,
    summary="Заказать приёмку или замер",
)
async def create_service_order(
    object_id: uuid.UUID,
    body: ServiceOrderCreate,
    current: Auth,
    request: Request,
    db: DbSession,
) -> ServiceOrderOut:
    access = await load_object(db, current, object_id, lock=True)
    if not access.can_order_services:
        raise Problem(403, "forbidden", "Заказать выезд может заказчик")
    obj = access.obj
    if obj.status in {ObjectStatus.cancelled, ObjectStatus.completed}:
        raise Problem(409, "object-closed", "Объект закрыт")
    if body.type == ServiceOrderType.survey and obj.status not in {
        ObjectStatus.draft,
        ObjectStatus.survey,
    }:
        raise Problem(409, "survey-not-needed", "Замер уже проведён")
    duplicate = await db.execute(
        select(ServiceOrder.id).where(
            ServiceOrder.object_id == obj.id,
            ServiceOrder.type == body.type,
            ServiceOrder.status.in_(ACTIVE),
        )
    )
    if duplicate.first() is not None:
        raise Problem(409, "order-exists", "Такой выезд уже заказан")
    comment = body.comment
    if body.preferred_at:
        wish = body.preferred_at.strftime("%d.%m.%Y %H:%M")
        comment = f"Удобное время: {wish}" + (f". {comment}" if comment else "")
    order = ServiceOrder(
        object_id=obj.id,
        type=body.type,
        price_kop=default_price(body.type),
        customer_comment=comment,
        created_by=current.id,
    )
    db.add(order)
    if body.type == ServiceOrderType.survey and obj.status == ObjectStatus.draft:
        ensure_transition("object", obj.status, ObjectStatus.survey)
        obj.status = ObjectStatus.survey
        audit(
            db,
            entity_type="object",
            entity_id=obj.id,
            action="object.status_changed",
            actor_id=current.id,
            ip=client_ip(request),
            old={"status": ObjectStatus.draft},
            new={"status": obj.status},
        )
    await db.flush()
    audit(
        db,
        entity_type="service_order",
        entity_id=order.id,
        action="service_order.created",
        actor_id=current.id,
        ip=client_ip(request),
        new={"type": order.type, "price_kop": order.price_kop, "object_id": obj.id},
    )
    await db.commit()
    await db.refresh(order)
    return await _out(db, order)


@router.get(
    "/objects/{object_id}/service-orders",
    response_model=list[ServiceOrderOut],
    summary="Выезды по объекту",
)
async def list_service_orders(
    object_id: uuid.UUID, current: Auth, db: DbSession
) -> list[ServiceOrderOut]:
    await load_object(db, current, object_id)
    rows = await db.execute(
        select(ServiceOrder)
        .where(ServiceOrder.object_id == object_id)
        .order_by(ServiceOrder.created_at.desc())
    )
    return [await _out(db, o) for o in rows.scalars()]


@router.get(
    "/service-orders",
    response_model=list[ServiceOrderOut],
    summary="Выезды: все (админ) или мои (инженер)",
)
async def my_service_orders(
    current: Auth, db: DbSession, status: ServiceOrderStatus | None = None
) -> list[ServiceOrderOut]:
    stmt = select(ServiceOrder)
    if not current.is_platform_admin:
        stmt = stmt.where(ServiceOrder.inspector_id == current.id)
    if status is not None:
        stmt = stmt.where(ServiceOrder.status == status)
    rows = await db.execute(stmt.order_by(ServiceOrder.created_at.desc()).limit(500))
    return [await _out(db, o) for o in rows.scalars()]


@router.post(
    "/service-orders/{order_id}/schedule",
    response_model=ServiceOrderOut,
    summary="Назначить инженера и время",
)
async def schedule(
    order_id: uuid.UUID,
    body: ServiceOrderSchedule,
    current: Auth,
    request: Request,
    db: DbSession,
) -> ServiceOrderOut:
    order, obj, _ = await _load(db, current, order_id)
    if not current.is_platform_admin:
        raise Problem(403, "forbidden", "Выезды назначает администратор платформы")
    is_inspector = (
        await db.execute(
            select(OrgMember.id)
            .join(Organization, Organization.id == OrgMember.org_id)
            .where(
                Organization.type == OrgType.platform,
                OrgMember.user_id == body.inspector_id,
                OrgMember.role == OrgRole.inspector,
            )
        )
    ).first()
    if is_inspector is None:
        raise Problem(422, "not-an-inspector", "Этот человек не инженер технадзора платформы")
    # Перенос уже оплаченного выезда не меняет статус оплаты.
    target = (
        ServiceOrderStatus.paid
        if order.status == ServiceOrderStatus.paid
        else ServiceOrderStatus.scheduled
    )
    _set_status(order, target)
    old = {"inspector_id": order.inspector_id, "slot_at": order.slot_at}
    order.inspector_id = body.inspector_id
    order.slot_at = body.slot_at
    if body.price_kop is not None and order.paid_at is None:
        order.price_kop = body.price_kop
    await ensure_assignment(db, obj.id, body.inspector_id, AssignmentRole.inspector)
    audit(
        db,
        entity_type="service_order",
        entity_id=order.id,
        action="service_order.scheduled",
        actor_id=current.id,
        ip=client_ip(request),
        old=old,
        new={
            "inspector_id": body.inspector_id,
            "slot_at": body.slot_at,
            "price_kop": order.price_kop,
        },
    )
    await db.commit()
    await db.refresh(order)
    return await _out(db, order)


@router.post(
    "/service-orders/{order_id}/mark-paid",
    response_model=ServiceOrderOut,
    summary="Отметить оплату (до подключения банка)",
)
async def mark_paid(
    order_id: uuid.UUID, current: Auth, request: Request, db: DbSession
) -> ServiceOrderOut:
    order, _, _ = await _load(db, current, order_id)
    if not current.is_platform_admin:
        raise Problem(403, "forbidden", "Оплату отмечает администратор платформы")
    if order.status == ServiceOrderStatus.paid:
        return await _out(db, order)
    _set_status(order, ServiceOrderStatus.paid)
    order.paid_at = utcnow()
    audit(
        db,
        entity_type="service_order",
        entity_id=order.id,
        action="service_order.paid",
        actor_id=current.id,
        ip=client_ip(request),
        new={"price_kop": order.price_kop},
    )
    await db.commit()
    await db.refresh(order)
    return await _out(db, order)


@router.post(
    "/service-orders/{order_id}/complete", response_model=ServiceOrderOut, summary="Завершить выезд"
)
async def complete(
    order_id: uuid.UUID,
    body: ServiceOrderComplete,
    current: Auth,
    request: Request,
    db: DbSession,
) -> ServiceOrderOut:
    order, obj, is_inspector = await _load(db, current, order_id)
    if not (is_inspector or current.is_platform_admin):
        raise Problem(403, "forbidden", "Завершить выезд может назначенный инженер")
    if order.status != ServiceOrderStatus.paid:
        raise Problem(
            409, "not-paid", "Выезд ещё не оплачен", "Завершить можно только оплаченный выезд."
        )
    _set_status(order, ServiceOrderStatus.completed)
    order.result_notes = body.result_notes
    order.completed_at = utcnow()
    if order.type == ServiceOrderType.survey and obj.status == ObjectStatus.survey:
        ensure_transition("object", obj.status, ObjectStatus.estimating)
        obj.status = ObjectStatus.estimating
        audit(
            db,
            entity_type="object",
            entity_id=obj.id,
            action="object.status_changed",
            actor_id=current.id,
            ip=client_ip(request),
            old={"status": ObjectStatus.survey},
            new={"status": obj.status},
        )
    audit(
        db,
        entity_type="service_order",
        entity_id=order.id,
        action="service_order.completed",
        actor_id=current.id,
        ip=client_ip(request),
        new={"result_notes": body.result_notes},
    )
    await db.commit()
    await db.refresh(order)
    return await _out(db, order)


@router.post(
    "/service-orders/{order_id}/cancel", response_model=ServiceOrderOut, summary="Отменить выезд"
)
async def cancel(
    order_id: uuid.UUID, current: Auth, request: Request, db: DbSession
) -> ServiceOrderOut:
    order, obj, _ = await _load(db, current, order_id)
    is_customer = obj.customer_id == current.id
    if not (is_customer or current.is_platform_admin):
        raise Problem(403, "forbidden", "Отменить выезд может заказчик или администратор")
    if order.status == ServiceOrderStatus.paid and not current.is_platform_admin:
        raise Problem(
            409,
            "paid-cancel-via-support",
            "Оплаченный выезд отменяет поддержка",
            "Напишите нам, вернём деньги по правилам оферты.",
        )
    _set_status(order, ServiceOrderStatus.cancelled)
    audit(
        db,
        entity_type="service_order",
        entity_id=order.id,
        action="service_order.cancelled",
        actor_id=current.id,
        ip=client_ip(request),
    )
    await db.commit()
    await db.refresh(order)
    return await _out(db, order)
