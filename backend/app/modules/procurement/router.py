"""Консьерж-закупки материалов и приёмка доставок (ТЗ 2.3, разделы 1.3, 3 и 5).

Путь заказа: прораб присылает заявку (текстом или голосом) → менеджер платформы собирает
корзину (draft) → предлагает заказчику (proposed) → заказчик подтверждает в течение 24 часов и
оплачивает (confirmed) → менеджер заказывает у поставщика (ordered) → поставщик или менеджер
отгружает (delivering) → прораб принимает доставку с фото накладной (delivered или
partially_delivered). Деньги заказчика лежат в резерве банка и уходят поставщику и платформе
только после приёмки. Разница между ценой для заказчика и ценой поставщика — маржа платформы.
"""

import json
import uuid
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from fastapi import APIRouter, File, Form, Query, Request, Response, UploadFile
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import ObjectAccess, load_object
from app.core.audit import audit
from app.core.config import get_settings
from app.core.deps import Auth, CurrentUser, DbSession, client_ip
from app.core.ids import uuid7
from app.core.problems import Problem
from app.core.security import utcnow
from app.core.storage import check_file_link, get_storage, signed_file_url
from app.domain.statuses import ensure_transition
from app.models import (
    Delivery,
    MaterialRequest,
    ObjectAssignment,
    Organization,
    Payment,
    PurchaseOrder,
    PurchaseOrderItem,
    RepairObject,
    Stage,
    User,
)
from app.models.enums import (
    AssignmentRole,
    DeliveryStatus,
    ObjectStatus,
    OrgRole,
    OrgType,
    PaymentStatus,
    PaymentType,
    PurchaseOrderStatus,
)
from app.modules.field.antifraud import NotAnImage, inspect_image
from app.modules.notifications import service as notes
from app.modules.payments import service as payments
from app.modules.payments.bank import Recipient, get_bank

router = APIRouter(tags=["procurement"])

CONFIRM_WINDOW = timedelta(hours=24)
CLOSED_OBJECT = {ObjectStatus.cancelled, ObjectStatus.completed}
AUDIO_TYPES = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mp4": "m4a",
    "audio/mpeg": "mp3",
    "audio/aac": "aac",
    "audio/wav": "wav",
    "audio/x-m4a": "m4a",
}
MAX_AUDIO_BYTES = 10 * 1024 * 1024
_PO = PurchaseOrderStatus


# ---------- схемы ----------


class RequestOut(BaseModel):
    id: uuid.UUID
    object_id: uuid.UUID
    address: str | None
    stage_id: uuid.UUID | None
    stage_title: str | None
    author_name: str | None
    text: str | None
    audio_url: str | None
    status: str
    created_at: datetime


class RequestStatusIn(BaseModel):
    status: Literal["new", "in_work", "done"]


class ItemIn(BaseModel):
    title: str = Field(min_length=2, max_length=500)
    sku: str | None = Field(default=None, max_length=100)
    qty: Decimal = Field(gt=0, max_digits=12, decimal_places=3)
    unit_price_kop: int = Field(ge=0)
    estimate_item_id: uuid.UUID | None = None


class OrderIn(BaseModel):
    stage_id: uuid.UUID | None = None
    request_id: uuid.UUID | None = None
    supplier_org_id: uuid.UUID | None = None
    supplier_cost_kop: int = Field(ge=0)
    comment: str | None = Field(default=None, max_length=4000)
    items: list[ItemIn] = Field(min_length=1, max_length=200)


class ItemOut(BaseModel):
    id: uuid.UUID
    title: str
    sku: str | None
    qty: Decimal
    unit_price_kop: int
    total_kop: int


class DeliveryOut(BaseModel):
    id: uuid.UUID
    status: DeliveryStatus
    delivered_at: datetime | None
    received_by_name: str | None
    discrepancies: list[dict[str, Any]]
    comment: str | None
    invoice_url: str | None


class OrderPaymentOut(BaseModel):
    id: uuid.UUID
    status: PaymentStatus
    pay_url: str | None


class OrderOut(BaseModel):
    id: uuid.UUID
    object_id: uuid.UUID
    address: str | None
    stage_id: uuid.UUID | None
    stage_title: str | None
    request_id: uuid.UUID | None
    supplier_org_id: uuid.UUID | None
    supplier_name: str | None
    status: PurchaseOrderStatus
    items: list[ItemOut]
    total_kop: int
    # Цена поставщика и маржа видны только сотрудникам платформы.
    supplier_cost_kop: int | None
    margin_kop: int | None
    comment: str | None
    reject_reason: str | None
    proposed_at: datetime | None
    expires_at: datetime | None
    confirmed_by_customer_at: datetime | None
    delivery_slot_at: datetime | None
    created_at: datetime
    payment: OrderPaymentOut | None
    deliveries: list[DeliveryOut]
    can_edit: bool
    can_confirm: bool
    can_order: bool
    can_ship: bool
    can_receive: bool
    can_complete: bool


class RejectIn(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)


class ShipIn(BaseModel):
    delivery_slot_at: datetime | None = None


class Discrepancy(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    expected: Decimal | None = None
    received: Decimal | None = None
    comment: str | None = Field(default=None, max_length=1000)


# ---------- доступ ----------


class OrderRights:
    def __init__(self, current: CurrentUser, access: ObjectAccess | None, order: PurchaseOrder):
        self.staff = current.is_platform_admin or current.has_platform_role(
            OrgRole.procurement_manager
        )
        self.customer = bool(access and access.is_customer)
        self.crew = bool(access and access.is_crew)
        self.supplier = bool(
            order.supplier_org_id
            and OrgRole.supplier_manager in current.roles_in_org(order.supplier_org_id)
        )


def _is_staff(current: CurrentUser) -> bool:
    return current.is_platform_admin or current.has_platform_role(OrgRole.procurement_manager)


async def _order_access(
    db: AsyncSession, current: CurrentUser, order_id: uuid.UUID, *, lock: bool = False
) -> tuple[PurchaseOrder, OrderRights]:
    order = await db.get(PurchaseOrder, order_id, with_for_update=lock)
    if order is None:
        raise Problem(404, "not-found", "Заказ не найден")
    access: ObjectAccess | None = None
    try:
        access = await load_object(db, current, order.object_id)
    except Problem:
        # Поставщик не видит объект целиком, только свои заказы.
        access = None
    rights = OrderRights(current, access, order)
    if access is None and not rights.supplier:
        raise Problem(404, "not-found", "Заказ не найден")
    return order, rights


def _expired(order: PurchaseOrder) -> bool:
    return (
        order.status == _PO.proposed
        and order.proposed_at is not None
        and order.proposed_at + CONFIRM_WINDOW < utcnow()
    )


def _item_total(qty: Decimal, price: int) -> int:
    return int((qty * price).quantize(Decimal(1), rounding=ROUND_HALF_UP))


async def _order_payment(db: AsyncSession, order_id: uuid.UUID) -> Payment | None:
    return (
        await db.execute(
            select(Payment)
            .where(
                Payment.purchase_order_id == order_id,
                Payment.status.notin_([PaymentStatus.failed, PaymentStatus.refunded]),
            )
            .order_by(Payment.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def order_out(
    db: AsyncSession, order: PurchaseOrder, rights: OrderRights, viewer: uuid.UUID
) -> OrderOut:
    obj = await db.get(RepairObject, order.object_id)
    stage = await db.get(Stage, order.stage_id) if order.stage_id else None
    supplier = await db.get(Organization, order.supplier_org_id) if order.supplier_org_id else None
    items = (
        await db.execute(
            select(PurchaseOrderItem)
            .where(PurchaseOrderItem.order_id == order.id)
            .order_by(PurchaseOrderItem.id)
        )
    ).scalars()
    deliveries = (
        await db.execute(
            select(Delivery, User)
            .outerjoin(User, User.id == Delivery.received_by)
            .where(Delivery.order_id == order.id)
            .order_by(Delivery.created_at)
        )
    ).all()
    payment = await _order_payment(db, order.id)
    pay_url = None
    if payment and payment.status == PaymentStatus.pending_funding and rights.customer:
        pay_url = payment.split.get("hold", {}).get("pay_url")
    held = payment is not None and payment.status == PaymentStatus.held
    status = order.status
    return OrderOut(
        id=order.id,
        object_id=order.object_id,
        address=obj.address_text if obj else None,
        stage_id=order.stage_id,
        stage_title=stage.title if stage else None,
        request_id=order.request_id,
        supplier_org_id=order.supplier_org_id,
        supplier_name=supplier.legal_name if supplier else None,
        status=status,
        items=[
            ItemOut(
                id=i.id,
                title=i.title,
                sku=i.sku,
                qty=i.qty,
                unit_price_kop=i.unit_price_kop,
                total_kop=_item_total(i.qty, i.unit_price_kop),
            )
            for i in items
        ],
        total_kop=order.total_kop,
        supplier_cost_kop=order.supplier_cost_kop if rights.staff else None,
        margin_kop=order.total_kop - order.supplier_cost_kop if rights.staff else None,
        comment=order.comment,
        reject_reason=order.reject_reason,
        proposed_at=order.proposed_at,
        expires_at=order.proposed_at + CONFIRM_WINDOW if order.proposed_at else None,
        confirmed_by_customer_at=order.confirmed_by_customer_at,
        delivery_slot_at=order.delivery_slot_at,
        created_at=order.created_at,
        payment=OrderPaymentOut(id=payment.id, status=payment.status, pay_url=pay_url)
        if payment
        else None,
        deliveries=[
            DeliveryOut(
                id=d.id,
                status=d.status,
                delivered_at=d.delivered_at,
                received_by_name=(u.full_name or u.phone) if u else None,
                discrepancies=d.discrepancies,
                comment=d.comment,
                invoice_url=signed_file_url("invoice", d.id, viewer) if d.invoice_key else None,
            )
            for d, u in deliveries
        ],
        can_edit=rights.staff and status == _PO.draft,
        can_confirm=rights.customer and status == _PO.proposed and not _expired(order),
        can_order=rights.staff and status == _PO.confirmed and held,
        can_ship=(rights.staff or rights.supplier)
        and status in (_PO.ordered, _PO.partially_delivered),
        can_receive=rights.crew and status == _PO.delivering,
        can_complete=rights.staff and status == _PO.partially_delivered,
    )


def _change(
    db: AsyncSession,
    order: PurchaseOrder,
    target: PurchaseOrderStatus,
    action: str,
    actor: uuid.UUID | None,
    ip: str | None = None,
    **extra: Any,
) -> None:
    ensure_transition("purchase_order", order.status, target)
    old = order.status
    order.status = target
    audit(
        db,
        entity_type="purchase_order",
        entity_id=order.id,
        action=action,
        actor_id=actor,
        ip=ip,
        is_system=actor is None,
        old={"status": old},
        new={"status": target, **extra},
    )


async def _crew_ids(db: AsyncSession, obj: RepairObject) -> list[uuid.UUID]:
    foremen = await db.execute(
        select(ObjectAssignment.user_id).where(
            ObjectAssignment.object_id == obj.id, ObjectAssignment.role == AssignmentRole.foreman
        )
    )
    bosses = await notes.org_member_ids(db, obj.contractor_org_id, [OrgRole.contractor_admin])
    return [*foremen.scalars(), *bosses]


async def _staff_ids(db: AsyncSession) -> list[uuid.UUID]:
    return await notes.platform_staff_ids(db, [OrgRole.procurement_manager])


# ---------- заявки прораба ----------


async def _request_out(db: AsyncSession, r: MaterialRequest, viewer: uuid.UUID) -> RequestOut:
    obj = await db.get(RepairObject, r.object_id)
    stage = await db.get(Stage, r.stage_id) if r.stage_id else None
    author = await db.get(User, r.created_by)
    return RequestOut(
        id=r.id,
        object_id=r.object_id,
        address=obj.address_text if obj else None,
        stage_id=r.stage_id,
        stage_title=stage.title if stage else None,
        author_name=(author.full_name or author.phone) if author else None,
        text=r.text,
        audio_url=signed_file_url("audio", r.id, viewer) if r.audio_key else None,
        status=r.status,
        created_at=r.created_at,
    )


@router.post(
    "/objects/{object_id}/material-requests",
    response_model=RequestOut,
    status_code=201,
    summary="Заявка на материалы (текст или голос)",
)
async def create_request(
    object_id: uuid.UUID,
    current: Auth,
    request: Request,
    db: DbSession,
    text: str | None = Form(default=None, max_length=4000),
    stage_id: uuid.UUID | None = Form(default=None),
    audio: UploadFile | None = File(default=None),
) -> RequestOut:
    access = await load_object(db, current, object_id)
    if not access.is_crew:
        raise Problem(403, "forbidden", "Заявку присылает прораб объекта")
    if access.obj.status in CLOSED_OBJECT:
        raise Problem(409, "object-closed", "Объект закрыт")
    text = (text or "").strip() or None
    if stage_id is not None:
        stage = await db.get(Stage, stage_id)
        if stage is None or stage.object_id != object_id:
            raise Problem(404, "not-found", "Этап не найден")
    req = MaterialRequest(
        id=uuid7(), object_id=object_id, stage_id=stage_id, created_by=current.id, text=text
    )
    if audio is not None and audio.filename:
        content_type = (audio.content_type or "").split(";")[0].strip()
        ext = AUDIO_TYPES.get(content_type)
        if ext is None:
            raise Problem(422, "bad-audio", "Неподдерживаемый формат записи")
        data = await audio.read(MAX_AUDIO_BYTES + 1)
        if len(data) > MAX_AUDIO_BYTES:
            raise Problem(413, "file-too-large", "Запись слишком длинная")
        if data:
            req.audio_key = f"requests/{object_id}/{req.id}.{ext}"
            req.audio_type = content_type
            await get_storage().put(req.audio_key, data, content_type)
    if req.text is None and req.audio_key is None:
        raise Problem(422, "empty-request", "Напишите или наговорите, что нужно")
    db.add(req)
    await db.flush()
    audit(
        db,
        entity_type="material_request",
        entity_id=req.id,
        action="material_request.created",
        actor_id=current.id,
        ip=client_ip(request),
        new={"text": req.text, "audio": bool(req.audio_key), "stage_id": stage_id},
    )
    notes.notify(
        db,
        await _staff_ids(db),
        "material_request",
        address=access.obj.address_text,
        url=notes.url("/procurement"),
    )
    await db.commit()
    return await _request_out(db, req, current.id)


@router.get(
    "/objects/{object_id}/material-requests",
    response_model=list[RequestOut],
    summary="Заявки на материалы по объекту",
)
async def object_requests(object_id: uuid.UUID, current: Auth, db: DbSession) -> list[RequestOut]:
    await load_object(db, current, object_id)
    rows = await db.execute(
        select(MaterialRequest)
        .where(MaterialRequest.object_id == object_id)
        .order_by(MaterialRequest.created_at.desc())
        .limit(100)
    )
    return [await _request_out(db, r, current.id) for r in rows.scalars()]


@router.get(
    "/material-requests", response_model=list[RequestOut], summary="Все заявки (менеджер закупок)"
)
async def all_requests(
    current: Auth, db: DbSession, open_only: bool = Query(default=True)
) -> list[RequestOut]:
    if not _is_staff(current):
        raise Problem(403, "forbidden", "Заявки видит менеджер закупок")
    stmt = select(MaterialRequest).order_by(MaterialRequest.created_at.desc()).limit(200)
    if open_only:
        stmt = stmt.where(MaterialRequest.status != "done")
    return [await _request_out(db, r, current.id) for r in (await db.execute(stmt)).scalars()]


@router.post(
    "/material-requests/{request_id}/status",
    response_model=RequestOut,
    summary="Отметить заявку (в работе / выполнена)",
)
async def set_request_status(
    request_id: uuid.UUID, body: RequestStatusIn, current: Auth, request: Request, db: DbSession
) -> RequestOut:
    if not _is_staff(current):
        raise Problem(403, "forbidden", "Заявки ведёт менеджер закупок")
    req = await db.get(MaterialRequest, request_id, with_for_update=True)
    if req is None:
        raise Problem(404, "not-found", "Заявка не найдена")
    old = req.status
    req.status = body.status
    audit(
        db,
        entity_type="material_request",
        entity_id=req.id,
        action="material_request.status",
        actor_id=current.id,
        ip=client_ip(request),
        old={"status": old},
        new={"status": body.status},
    )
    await db.commit()
    return await _request_out(db, req, current.id)


@router.get(
    "/files/{kind}/{file_id}", summary="Файл по подписанной ссылке", response_class=Response
)
async def file_content(
    kind: Literal["audio", "invoice"],
    file_id: uuid.UUID,
    exp: int,
    v: uuid.UUID,
    sig: str,
    db: DbSession,
) -> Response:
    if not check_file_link(kind, file_id, exp, v, sig):
        raise Problem(403, "link-expired", "Ссылка устарела", "Обновите страницу.")
    if kind == "audio":
        req = await db.get(MaterialRequest, file_id)
        key, content_type = (req.audio_key, req.audio_type) if req else (None, None)
    else:
        delivery = await db.get(Delivery, file_id)
        key = delivery.invoice_key if delivery else None
        content_type = "image/jpeg"
        if key and key.endswith(".png"):
            content_type = "image/png"
        elif key and key.endswith(".webp"):
            content_type = "image/webp"
    if not key:
        raise Problem(404, "not-found", "Файл не найден")
    return Response(
        await get_storage().get(key),
        media_type=content_type or "application/octet-stream",
        headers={"Cache-Control": "private, max-age=600", "X-Content-Type-Options": "nosniff"},
    )


# ---------- корзина и заказ ----------


async def _apply_items(db: AsyncSession, order: PurchaseOrder, body: OrderIn) -> None:
    if body.supplier_org_id is not None:
        supplier = await db.get(Organization, body.supplier_org_id)
        if supplier is None or supplier.type != OrgType.supplier:
            raise Problem(422, "bad-supplier", "Поставщик не найден")
    if body.stage_id is not None:
        stage = await db.get(Stage, body.stage_id)
        if stage is None or stage.object_id != order.object_id:
            raise Problem(404, "not-found", "Этап не найден")
    old_items = await db.execute(
        select(PurchaseOrderItem).where(PurchaseOrderItem.order_id == order.id)
    )
    for item in old_items.scalars():
        await db.delete(item)
    total = 0
    for i in body.items:
        db.add(
            PurchaseOrderItem(
                order_id=order.id,
                estimate_item_id=i.estimate_item_id,
                title=i.title.strip(),
                sku=i.sku,
                qty=i.qty,
                unit_price_kop=i.unit_price_kop,
            )
        )
        total += _item_total(i.qty, i.unit_price_kop)
    if total <= 0:
        raise Problem(422, "empty-order", "Сумма корзины должна быть больше нуля")
    if body.supplier_cost_kop > total:
        raise Problem(
            422,
            "negative-margin",
            "Цена поставщика больше цены для заказчика",
            "Платформа не продаёт материалы в минус.",
        )
    order.stage_id = body.stage_id
    order.supplier_org_id = body.supplier_org_id
    order.supplier_cost_kop = body.supplier_cost_kop
    order.total_kop = total
    order.platform_fee_kop = total - body.supplier_cost_kop
    order.comment = (body.comment or "").strip() or None


@router.post(
    "/objects/{object_id}/purchase-orders",
    response_model=OrderOut,
    status_code=201,
    summary="Собрать корзину материалов (менеджер)",
)
async def create_order(
    object_id: uuid.UUID, body: OrderIn, current: Auth, request: Request, db: DbSession
) -> OrderOut:
    access = await load_object(db, current, object_id)
    if not access.can_buy:
        raise Problem(403, "forbidden", "Корзину собирает менеджер закупок")
    if access.obj.status in CLOSED_OBJECT:
        raise Problem(409, "object-closed", "Объект закрыт")
    order = PurchaseOrder(id=uuid7(), object_id=object_id, created_by=current.id, status=_PO.draft)
    db.add(order)
    await db.flush()
    await _apply_items(db, order, body)
    if body.request_id is not None:
        req = await db.get(MaterialRequest, body.request_id, with_for_update=True)
        if req is None or req.object_id != object_id:
            raise Problem(404, "not-found", "Заявка не найдена")
        order.request_id = req.id
        if req.status == "new":
            req.status = "in_work"
    audit(
        db,
        entity_type="purchase_order",
        entity_id=order.id,
        action="purchase_order.created",
        actor_id=current.id,
        ip=client_ip(request),
        new={"total_kop": order.total_kop, "supplier_cost_kop": order.supplier_cost_kop},
    )
    await db.commit()
    return await order_out(db, order, OrderRights(current, access, order), current.id)


@router.put("/purchase-orders/{order_id}", response_model=OrderOut, summary="Изменить корзину")
async def update_order(
    order_id: uuid.UUID, body: OrderIn, current: Auth, request: Request, db: DbSession
) -> OrderOut:
    order, rights = await _order_access(db, current, order_id, lock=True)
    if not rights.staff:
        raise Problem(403, "forbidden", "Корзину собирает менеджер закупок")
    if order.status != _PO.draft:
        raise Problem(409, "order-locked", "Корзина уже отправлена заказчику")
    old = {"total_kop": order.total_kop, "supplier_cost_kop": order.supplier_cost_kop}
    await _apply_items(db, order, body)
    audit(
        db,
        entity_type="purchase_order",
        entity_id=order.id,
        action="purchase_order.updated",
        actor_id=current.id,
        ip=client_ip(request),
        old=old,
        new={"total_kop": order.total_kop, "supplier_cost_kop": order.supplier_cost_kop},
    )
    await db.commit()
    return await order_out(db, order, rights, current.id)


@router.post(
    "/purchase-orders/{order_id}/propose",
    response_model=OrderOut,
    summary="Отправить корзину заказчику",
)
async def propose(order_id: uuid.UUID, current: Auth, request: Request, db: DbSession) -> OrderOut:
    order, rights = await _order_access(db, current, order_id, lock=True)
    if not rights.staff:
        raise Problem(403, "forbidden", "Корзину отправляет менеджер закупок")
    _change(db, order, _PO.proposed, "purchase_order.proposed", current.id, client_ip(request))
    order.proposed_at = utcnow()
    obj = await db.get(RepairObject, order.object_id)
    assert obj is not None
    notes.notify(
        db,
        [obj.customer_id],
        "order_proposed",
        amount=notes.rub(order.total_kop),
        url=notes.url(f"/objects/{obj.id}"),
    )
    await db.commit()
    return await order_out(db, order, rights, current.id)


@router.post(
    "/purchase-orders/{order_id}/confirm",
    response_model=OrderOut,
    summary="Подтвердить корзину и оплатить (заказчик)",
)
async def confirm(order_id: uuid.UUID, current: Auth, request: Request, db: DbSession) -> OrderOut:
    order, rights = await _order_access(db, current, order_id, lock=True)
    if not rights.customer:
        raise Problem(403, "forbidden", "Корзину подтверждает заказчик")
    if _expired(order):
        _change(db, order, _PO.expired, "purchase_order.expired", None)
        await db.commit()
        raise Problem(
            409,
            "order-expired",
            "Время на подтверждение вышло",
            "Попросите менеджера собрать корзину заново: цены могли измениться.",
        )
    ip = client_ip(request)
    _change(db, order, _PO.confirmed, "purchase_order.confirmed", current.id, ip)
    order.confirmed_by_customer_at = utcnow()
    obj = await db.get(RepairObject, order.object_id)
    assert obj is not None
    payment = Payment(
        object_id=order.object_id,
        purchase_order_id=order.id,
        type=PaymentType.hold,
        idempotency_key=request.headers.get("idempotency-key") or str(uuid.uuid4()),
        amount_kop=order.total_kop,
        split={
            "breakdown": {
                "material_kop": order.total_kop,
                "supplier_kop": order.supplier_cost_kop,
                "platform_margin_kop": order.total_kop - order.supplier_cost_kop,
            }
        },
        status=PaymentStatus.created,
    )
    db.add(payment)
    await db.flush()
    result = await get_bank().create_hold(
        payment.id, payment.amount_kop, f"Материалы для ремонта, {obj.address_text}"
    )
    payment.bank_op_id = result.bank_op_id
    payment.split = {
        **payment.split,
        "hold": {"pay_url": result.pay_url, "expires_at": result.expires_at.isoformat()},
    }
    ensure_transition("payment", payment.status, PaymentStatus.pending_funding)
    payment.status = PaymentStatus.pending_funding
    audit(
        db,
        entity_type="payment",
        entity_id=payment.id,
        action="payment.hold_created",
        actor_id=current.id,
        ip=ip,
        new={"amount_kop": payment.amount_kop, "purchase_order_id": order.id},
    )
    await db.commit()
    return await order_out(db, order, rights, current.id)


@router.post(
    "/purchase-orders/{order_id}/reject",
    response_model=OrderOut,
    summary="Отказаться от корзины (заказчик)",
)
async def reject(
    order_id: uuid.UUID, body: RejectIn, current: Auth, request: Request, db: DbSession
) -> OrderOut:
    order, rights = await _order_access(db, current, order_id, lock=True)
    if not rights.customer:
        raise Problem(403, "forbidden", "От корзины отказывается заказчик")
    _change(
        db,
        order,
        _PO.rejected,
        "purchase_order.rejected",
        current.id,
        client_ip(request),
        reason=body.reason,
    )
    order.reject_reason = body.reason.strip()
    await db.commit()
    return await order_out(db, order, rights, current.id)


@router.post(
    "/purchase-orders/{order_id}/order",
    response_model=OrderOut,
    summary="Заказано у поставщика (менеджер)",
)
async def place(order_id: uuid.UUID, current: Auth, request: Request, db: DbSession) -> OrderOut:
    order, rights = await _order_access(db, current, order_id, lock=True)
    if not rights.staff:
        raise Problem(403, "forbidden", "Заказ у поставщика оформляет менеджер закупок")
    payment = await _order_payment(db, order.id)
    if payment is None or payment.status != PaymentStatus.held:
        raise Problem(409, "not-paid", "Заказчик ещё не оплатил корзину")
    _change(db, order, _PO.ordered, "purchase_order.ordered", current.id, client_ip(request))
    await db.commit()
    return await order_out(db, order, rights, current.id)


@router.post(
    "/purchase-orders/{order_id}/ship",
    response_model=OrderOut,
    summary="Материалы отгружены (поставщик или менеджер)",
)
async def ship(
    order_id: uuid.UUID, body: ShipIn, current: Auth, request: Request, db: DbSession
) -> OrderOut:
    order, rights = await _order_access(db, current, order_id, lock=True)
    if not (rights.staff or rights.supplier):
        raise Problem(403, "forbidden", "Отгрузку отмечает поставщик или менеджер закупок")
    _change(
        db,
        order,
        _PO.delivering,
        "purchase_order.shipped",
        current.id,
        client_ip(request),
        delivery_slot_at=body.delivery_slot_at.isoformat() if body.delivery_slot_at else None,
    )
    order.delivery_slot_at = body.delivery_slot_at
    obj = await db.get(RepairObject, order.object_id)
    assert obj is not None
    notes.notify(db, await _crew_ids(db, obj), "order_shipped", address=obj.address_text)
    await db.commit()
    return await order_out(db, order, rights, current.id)


async def _release(db: AsyncSession, order: PurchaseOrder, actor: uuid.UUID | None) -> None:
    """Приёмка завершена: резерв уходит поставщику (его цена) и платформе (маржа)."""
    payment = (
        await db.execute(
            select(Payment)
            .where(Payment.purchase_order_id == order.id, Payment.status == PaymentStatus.held)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if payment is None:
        raise Problem(409, "not-paid", "Нет оплаченного резерва по заказу")
    platform_inn = get_settings().platform_inn
    if order.supplier_org_id:
        supplier = await db.get(Organization, order.supplier_org_id)
        assert supplier is not None
        recipients = [Recipient("supplier", supplier.inn, order.supplier_cost_kop)]
        margin = payment.amount_kop - order.supplier_cost_kop
        if margin:
            recipients.append(Recipient("platform", platform_inn, margin))
    else:
        # Магазин без договора с платформой: платформа оплатила его сама, деньги идут ей.
        recipients = [Recipient("platform", platform_inn, payment.amount_kop)]
    if sum(r.amount_kop for r in recipients) != payment.amount_kop:
        raise Problem(409, "split-mismatch", "Сумма выплат не совпадает с резервом")
    payment.split = {**payment.split, "recipients": [r.__dict__ for r in recipients]}
    payments._set(
        db,
        "payment",
        payment,
        PaymentStatus.release_requested,
        "payment.release_requested",
        actor,
        purchase_order_id=order.id,
    )
    await db.flush()
    await payments.run(db, await get_bank().release(payment.id, recipients), actor)


async def _delivered(
    db: AsyncSession, order: PurchaseOrder, actor: uuid.UUID, ip: str | None
) -> None:
    _change(db, order, _PO.delivered, "purchase_order.delivered", actor, ip)
    await _release(db, order, actor)
    if order.request_id:
        req = await db.get(MaterialRequest, order.request_id)
        if req is not None:
            req.status = "done"
    obj = await db.get(RepairObject, order.object_id)
    assert obj is not None
    notes.notify(
        db,
        [obj.customer_id, *await _staff_ids(db)],
        "order_delivered",
        address=obj.address_text,
        url=notes.url(f"/objects/{obj.id}"),
    )


@router.post(
    "/purchase-orders/{order_id}/deliveries",
    response_model=OrderOut,
    status_code=201,
    summary="Принять доставку с фото накладной (прораб)",
)
async def receive(
    order_id: uuid.UUID,
    current: Auth,
    request: Request,
    db: DbSession,
    result: Literal["accepted", "discrepancies", "rejected"] = Form(...),
    invoice: UploadFile = File(..., description="Фото накладной"),
    discrepancies: str = Form(default="[]", description="JSON: [{title, expected, received}]"),
    comment: str | None = Form(default=None, max_length=2000),
) -> OrderOut:
    order, rights = await _order_access(db, current, order_id, lock=True)
    if not rights.crew:
        raise Problem(403, "forbidden", "Доставку принимает прораб объекта")
    if order.status != _PO.delivering:
        raise Problem(409, "not-shipped", "Этот заказ сейчас не в доставке")
    try:
        found = [Discrepancy.model_validate(d) for d in json.loads(discrepancies or "[]")]
    except (ValueError, TypeError, ValidationError):
        raise Problem(422, "bad-discrepancies", "Не удалось прочитать расхождения") from None
    if result == "discrepancies" and not found:
        raise Problem(422, "no-discrepancies", "Опишите, чего не хватает или что не так")
    comment = (comment or "").strip() or None
    if result == "rejected" and not comment:
        raise Problem(422, "no-comment", "Напишите, почему доставка не принята")
    s = get_settings()
    data = await invoice.read(s.max_photo_bytes + 1)
    if len(data) > s.max_photo_bytes:
        raise Problem(413, "file-too-large", "Фото слишком большое")
    try:
        info = inspect_image(data)
    except NotAnImage as exc:
        raise Problem(422, "not-an-image", str(exc)) from None
    delivery = Delivery(
        id=uuid7(),
        order_id=order.id,
        delivered_at=utcnow(),
        received_by=current.id,
        discrepancies=[d.model_dump(mode="json") for d in found],
        comment=comment,
        status={
            "accepted": DeliveryStatus.accepted,
            "discrepancies": DeliveryStatus.accepted_with_discrepancies,
            "rejected": DeliveryStatus.rejected,
        }[result],
    )
    delivery.invoice_key = f"invoices/{order.object_id}/{delivery.id}.{info.ext}"
    await get_storage().put(delivery.invoice_key, data, info.content_type)
    db.add(delivery)
    await db.flush()
    ip = client_ip(request)
    audit(
        db,
        entity_type="delivery",
        entity_id=delivery.id,
        action="delivery.received",
        actor_id=current.id,
        ip=ip,
        new={"order_id": order.id, "status": delivery.status, "discrepancies": len(found)},
    )
    if result == "accepted":
        await _delivered(db, order, current.id, ip)
    elif result == "discrepancies":
        _change(db, order, _PO.partially_delivered, "purchase_order.partial", current.id, ip)
    if result != "accepted":
        obj = await db.get(RepairObject, order.object_id)
        notes.notify(
            db,
            await _staff_ids(db),
            "delivery_problem",
            address=obj.address_text if obj else "",
            result="есть расхождения" if result == "discrepancies" else "не принята",
            url=notes.url("/procurement"),
        )
    await db.commit()
    return await order_out(db, order, rights, current.id)


@router.post(
    "/purchase-orders/{order_id}/complete",
    response_model=OrderOut,
    summary="Закрыть заказ после расхождений (менеджер)",
)
async def complete(order_id: uuid.UUID, current: Auth, request: Request, db: DbSession) -> OrderOut:
    """Расхождения урегулированы с поставщиком (довезли, заменили, сделали скидку на следующий
    заказ) — менеджер закрывает заказ, и резерв уходит по разделению."""
    order, rights = await _order_access(db, current, order_id, lock=True)
    if not rights.staff:
        raise Problem(403, "forbidden", "Заказ закрывает менеджер закупок")
    await _delivered(db, order, current.id, client_ip(request))
    await db.commit()
    return await order_out(db, order, rights, current.id)


# ---------- списки ----------


@router.get(
    "/objects/{object_id}/purchase-orders",
    response_model=list[OrderOut],
    summary="Заказы материалов по объекту",
)
async def object_orders(object_id: uuid.UUID, current: Auth, db: DbSession) -> list[OrderOut]:
    access = await load_object(db, current, object_id)
    stmt = (
        select(PurchaseOrder)
        .where(PurchaseOrder.object_id == object_id)
        .order_by(PurchaseOrder.created_at.desc())
    )
    if not (access.is_admin or access.is_procurement):
        # Черновики корзины видит только менеджер.
        stmt = stmt.where(PurchaseOrder.status != _PO.draft)
    out = []
    for order in (await db.execute(stmt)).scalars():
        out.append(await order_out(db, order, OrderRights(current, access, order), current.id))
    return out


@router.get(
    "/purchase-orders",
    response_model=list[OrderOut],
    summary="Заказы материалов (менеджер закупок или поставщик)",
)
async def list_orders(
    current: Auth, db: DbSession, active_only: bool = Query(default=True)
) -> list[OrderOut]:
    stmt = select(PurchaseOrder).order_by(PurchaseOrder.created_at.desc()).limit(200)
    if not _is_staff(current):
        supplier_orgs = [
            org.id
            for member, org in current.memberships
            if org.type == OrgType.supplier and member.role == OrgRole.supplier_manager
        ]
        if not supplier_orgs:
            raise Problem(403, "forbidden", "Список заказов видят менеджер закупок и поставщики")
        stmt = stmt.where(
            PurchaseOrder.supplier_org_id.in_(supplier_orgs),
            PurchaseOrder.status.notin_([_PO.draft, _PO.proposed]),
        )
    if active_only:
        stmt = stmt.where(PurchaseOrder.status.notin_([_PO.delivered, _PO.expired, _PO.rejected]))
    out = []
    for order in (await db.execute(stmt)).scalars():
        _, rights = await _order_access(db, current, order.id)
        out.append(await order_out(db, order, rights, current.id))
    return out


@router.get("/purchase-orders/{order_id}", response_model=OrderOut, summary="Заказ материалов")
async def get_order(order_id: uuid.UUID, current: Auth, db: DbSession) -> OrderOut:
    order, rights = await _order_access(db, current, order_id)
    if order.status == _PO.draft and not rights.staff:
        raise Problem(404, "not-found", "Заказ не найден")
    return await order_out(db, order, rights, current.id)


async def expire_orders(db: AsyncSession) -> int:
    """Фоновая задача: корзины, которые заказчик не подтвердил за 24 часа, сгорают."""
    rows = await db.execute(
        select(PurchaseOrder)
        .where(
            PurchaseOrder.status == _PO.proposed,
            PurchaseOrder.proposed_at < utcnow() - CONFIRM_WINDOW,
        )
        .with_for_update(skip_locked=True)
    )
    n = 0
    for order in rows.scalars():
        _change(db, order, _PO.expired, "purchase_order.expired", None)
        n += 1
    await db.commit()
    return n
