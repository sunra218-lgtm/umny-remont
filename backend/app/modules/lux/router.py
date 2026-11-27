"""«Люкс ремонт»: премиальный пакет объекта, VIP-подрядчики, персональный менеджер,
дизайнерская мебель со всего мира с доставкой.

Заказ мебели идёт через менеджера: заказчик собирает подборку (из каталога или «найдите мне»
по описанию и ссылке) → менеджер фиксирует цену, доставку, таможню и срок → заказчик
подтверждает → оплата → фабрика → путь → объект → сборка VIP-мастерами.
"""

import uuid
from datetime import date, datetime

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.access import ObjectAccess, load_object
from app.core.audit import audit
from app.core.deps import Auth, CurrentUser, DbSession, client_ip
from app.core.ids import uuid7
from app.core.problems import Problem
from app.core.security import utcnow
from app.domain.design import STYLE_KEYS
from app.domain.lux import CATEGORIES, COUNTRIES, ORDER_STATUSES, to_rub_kop
from app.models import (
    FurnitureEvent,
    FurnitureItem,
    FurnitureOrder,
    FurnitureOrderItem,
    Organization,
    RepairObject,
    User,
)
from app.models.enums import ObjectStatus, OrgRole, OrgType
from app.modules.lux import integrations as ext
from app.modules.lux import service
from app.modules.lux.service import LogisticsEstimate
from app.modules.notifications import service as notes

router = APIRouter(tags=["lux"])

MANAGER_ROLES = (OrgRole.platform_admin, OrgRole.procurement_manager)
# Пакет можно сменить, пока ремонт не начался.
PACKAGE_OPEN = {
    ObjectStatus.draft,
    ObjectStatus.survey,
    ObjectStatus.estimating,
    ObjectStatus.contracting,
}


def _is_manager(current: CurrentUser) -> bool:
    return current.has_platform_role(*MANAGER_ROLES)


# ---------- справочники ----------


class LuxInfo(BaseModel):
    categories: dict[str, str]
    countries: dict[str, str]
    statuses: dict[str, str]
    partners: bool
    logistics: bool


@router.get("/lux/info", response_model=LuxInfo, summary="Справочники «Люкс ремонта»")
async def info(current: Auth) -> LuxInfo:
    return LuxInfo(
        categories=CATEGORIES,
        countries=COUNTRIES,
        statuses=ORDER_STATUSES,
        partners=ext.partners_enabled(),
        logistics=ext.logistics_enabled(),
    )


# ---------- пакет объекта ----------


class Person(BaseModel):
    id: uuid.UUID
    name: str | None
    phone: str


class LuxOut(BaseModel):
    package: str
    manager: Person | None
    contractor_vip: bool | None
    can_change: bool
    can_assign: bool


async def _lux_out(db: DbSession, access: ObjectAccess, current: CurrentUser) -> LuxOut:
    obj = access.obj
    m = await db.get(User, obj.lux_manager_id) if obj.lux_manager_id else None
    org = await db.get(Organization, obj.contractor_org_id) if obj.contractor_org_id else None
    return LuxOut(
        package=obj.package,
        manager=Person(id=m.id, name=m.full_name, phone=m.phone) if m else None,
        contractor_vip=(org.vip_at is not None) if org else None,
        can_change=(access.is_customer or access.is_admin) and obj.status in PACKAGE_OPEN,
        can_assign=access.is_admin,
    )


@router.get("/objects/{object_id}/lux", response_model=LuxOut, summary="Пакет ремонта")
async def get_lux(object_id: uuid.UUID, current: Auth, db: DbSession) -> LuxOut:
    access = await load_object(db, current, object_id)
    return await _lux_out(db, access, current)


class PackageIn(BaseModel):
    package: str = Field(pattern="^(standard|lux)$")


@router.put("/objects/{object_id}/package", response_model=LuxOut, summary="Выбрать пакет")
async def set_package(
    object_id: uuid.UUID, body: PackageIn, current: Auth, request: Request, db: DbSession
) -> LuxOut:
    access = await load_object(db, current, object_id, lock=True)
    if not (access.is_customer or access.is_admin):
        raise Problem(403, "forbidden", "Пакет выбирает заказчик")
    obj = access.obj
    if obj.status not in PACKAGE_OPEN:
        raise Problem(409, "object-locked", "Пакет меняется до начала ремонта")
    if body.package == obj.package:
        return await _lux_out(db, access, current)
    if body.package == "lux" and obj.contractor_org_id:
        org = await db.get(Organization, obj.contractor_org_id)
        if org is not None and org.vip_at is None:
            raise Problem(
                409,
                "contractor-not-vip",
                "Подрядчик объекта не из VIP-мастеров",
                "Администратор подберёт VIP-подрядчика, после этого можно перейти на «Люкс».",
            )
    old = obj.package
    obj.package = body.package
    audit(
        db,
        entity_type="object",
        entity_id=obj.id,
        action="object.package_set",
        actor_id=current.id,
        ip=client_ip(request),
        old={"package": old},
        new={"package": body.package},
    )
    if body.package == "lux" and obj.lux_manager_id is None:
        notes.notify(
            db,
            await notes.platform_staff_ids(db, [OrgRole.platform_admin]),
            "lux_requested",
            address=obj.address_text,
            url=notes.url(f"/objects/{obj.id}"),
        )
    await db.commit()
    return await _lux_out(db, access, current)


class ManagerIn(BaseModel):
    user_id: uuid.UUID | None


@router.put(
    "/objects/{object_id}/lux-manager", response_model=LuxOut, summary="Персональный менеджер"
)
async def set_manager(
    object_id: uuid.UUID, body: ManagerIn, current: Auth, request: Request, db: DbSession
) -> LuxOut:
    access = await load_object(db, current, object_id, lock=True)
    if not access.is_admin:
        raise Problem(403, "forbidden", "Менеджера назначает администратор")
    obj = access.obj
    if body.user_id is not None:
        if obj.package != "lux":
            raise Problem(409, "not-lux", "Персональный менеджер — в пакете «Люкс»")
        if body.user_id not in await notes.platform_staff_ids(db, MANAGER_ROLES):
            raise Problem(422, "not-staff", "Менеджер — сотрудник платформы")
    old = obj.lux_manager_id
    obj.lux_manager_id = body.user_id
    audit(
        db,
        entity_type="object",
        entity_id=obj.id,
        action="object.lux_manager_set",
        actor_id=current.id,
        ip=client_ip(request),
        old={"user_id": old},
        new={"user_id": body.user_id},
    )
    if body.user_id and body.user_id != old:
        notes.notify(
            db,
            [body.user_id],
            "lux_manager",
            address=obj.address_text,
            url=notes.url(f"/objects/{obj.id}"),
        )
    await db.commit()
    return await _lux_out(db, access, current)


@router.get("/lux/managers", response_model=list[Person], summary="Кого назначить менеджером")
async def managers(current: Auth, db: DbSession) -> list[Person]:
    if not current.is_platform_admin:
        raise Problem(403, "forbidden", "Только администратор")
    ids = await notes.platform_staff_ids(db, MANAGER_ROLES)
    users = (await db.execute(select(User).where(User.id.in_(ids)))).scalars()
    return [Person(id=u.id, name=u.full_name, phone=u.phone) for u in users]


class VipIn(BaseModel):
    vip: bool


@router.put("/contractors/{org_id}/vip", summary="VIP-подрядчик")
async def set_vip(
    org_id: uuid.UUID, body: VipIn, current: Auth, request: Request, db: DbSession
) -> dict[str, bool]:
    if not current.is_platform_admin:
        raise Problem(403, "forbidden", "VIP-статус даёт администратор")
    org = await db.get(Organization, org_id, with_for_update=True)
    if org is None or org.deleted_at is not None or org.type != OrgType.contractor:
        raise Problem(404, "not-found", "Подрядчик не найден")
    if body.vip and org.verified_at is None:
        raise Problem(409, "org-not-verified", "Сначала проверьте подрядчика")
    was = org.vip_at is not None
    if body.vip != was:
        org.vip_at = utcnow() if body.vip else None
        audit(
            db,
            entity_type="organization",
            entity_id=org.id,
            action="organization.vip_set",
            actor_id=current.id,
            ip=client_ip(request),
            old={"vip": was},
            new={"vip": body.vip},
        )
        await db.commit()
    return {"vip": body.vip}


# ---------- каталог мебели ----------


class ItemOut(BaseModel):
    id: uuid.UUID
    source: str
    title: str
    brand: str | None
    designer: str | None
    category: str
    country: str
    styles: list[str]
    description: str | None
    dimensions: str | None
    price: int
    currency: str
    price_rub_kop: int
    volume_l: int
    weight_kg: int
    lead_time_days: int
    image_url: str | None
    is_active: bool


def _item_out(i: FurnitureItem) -> ItemOut:
    return ItemOut.model_validate(i, from_attributes=True)


@router.get("/furniture", response_model=list[ItemOut], summary="Каталог дизайнерской мебели")
async def catalog(
    current: Auth,
    db: DbSession,
    q: str | None = Query(default=None, max_length=100),
    category: str | None = None,
    country: str | None = None,
    style: str | None = None,
    max_rub: int | None = Query(default=None, gt=0),
    all: bool = False,
) -> list[ItemOut]:
    stmt = select(FurnitureItem).order_by(FurnitureItem.price_rub_kop.desc()).limit(200)
    if not (all and _is_manager(current)):
        stmt = stmt.where(FurnitureItem.is_active.is_(True))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            FurnitureItem.title.ilike(like)
            | FurnitureItem.brand.ilike(like)
            | FurnitureItem.designer.ilike(like)
            | FurnitureItem.description.ilike(like)
        )
    if category:
        stmt = stmt.where(FurnitureItem.category == category)
    if country:
        stmt = stmt.where(FurnitureItem.country == country)
    if style:
        stmt = stmt.where(FurnitureItem.styles.contains([style]))
    if max_rub:
        stmt = stmt.where(FurnitureItem.price_rub_kop <= max_rub * 100)
    return [_item_out(i) for i in (await db.execute(stmt)).scalars()]


@router.get(
    "/furniture/partners", response_model=list[ItemOut], summary="Поиск у поставщиков-партнёров"
)
async def partners(
    current: Auth,
    db: DbSession,
    q: str = Query(min_length=2, max_length=100),
    category: str | None = None,
) -> list[ItemOut]:
    if not ext.partners_enabled():
        raise Problem(409, "partners-off", "Поиск у партнёров пока не подключён")
    try:
        found = await ext.search_partners(q.strip(), category)
    except Exception:  # noqa: BLE001
        raise Problem(502, "partners-failed", "Поставщики не ответили, попробуйте позже") from None
    out = []
    for p in found:
        if p.category not in CATEGORIES or p.country not in COUNTRIES:
            continue
        item = (
            await db.execute(
                select(FurnitureItem).where(
                    FurnitureItem.source == p.supplier, FurnitureItem.external_id == p.id
                )
            )
        ).scalar_one_or_none()
        if item is None:
            item = FurnitureItem(id=uuid7(), source=p.supplier, external_id=p.id)
            db.add(item)
        for k in (
            "title",
            "brand",
            "designer",
            "category",
            "country",
            "description",
            "dimensions",
            "price",
            "currency",
            "volume_l",
            "weight_kg",
            "lead_time_days",
            "image_url",
        ):
            setattr(item, k, getattr(p, k))
        item.styles = item.styles or []
        item.is_active = True
        item.price_rub_kop = to_rub_kop(p.price, p.currency)
        out.append(item)
    await db.commit()
    return [_item_out(i) for i in out]


class ItemIn(BaseModel):
    title: str = Field(min_length=2, max_length=300)
    brand: str | None = Field(default=None, max_length=150)
    designer: str | None = Field(default=None, max_length=150)
    category: str
    country: str
    styles: list[str] = Field(default_factory=list)
    description: str | None = Field(default=None, max_length=5000)
    dimensions: str | None = Field(default=None, max_length=100)
    price: int = Field(gt=0, le=10**12)
    currency: str = Field(min_length=3, max_length=3)
    price_rub_kop: int | None = Field(default=None, gt=0, le=10**12)
    volume_l: int = Field(default=500, gt=0, le=100_000)
    weight_kg: int = Field(default=50, gt=0, le=10_000)
    lead_time_days: int = Field(default=60, ge=1, le=730)
    image_url: str | None = Field(default=None, max_length=500, pattern=r"^https://")
    is_active: bool = True


def _check_item(body: ItemIn) -> None:
    if body.category not in CATEGORIES:
        raise Problem(422, "unknown-category", "Неизвестная категория")
    if body.country not in COUNTRIES:
        raise Problem(422, "unknown-country", "Неизвестная страна")
    if any(s not in STYLE_KEYS for s in body.styles):
        raise Problem(422, "unknown-style", "Неизвестный стиль")


def _apply_item(item: FurnitureItem, body: ItemIn) -> None:
    data = body.model_dump()
    rub = data.pop("price_rub_kop")
    for k, v in data.items():
        setattr(item, k, v)
    item.price_rub_kop = rub or to_rub_kop(body.price, body.currency)


@router.post("/furniture", response_model=ItemOut, status_code=201, summary="Добавить в каталог")
async def add_item(body: ItemIn, current: Auth, request: Request, db: DbSession) -> ItemOut:
    if not _is_manager(current):
        raise Problem(403, "forbidden", "Каталог ведут менеджеры платформы")
    _check_item(body)
    item = FurnitureItem(id=uuid7(), source="catalog")
    _apply_item(item, body)
    db.add(item)
    audit(
        db,
        entity_type="furniture_item",
        entity_id=item.id,
        action="furniture_item.created",
        actor_id=current.id,
        ip=client_ip(request),
        new={"title": item.title, "price_rub_kop": item.price_rub_kop},
    )
    await db.commit()
    return _item_out(item)


@router.put("/furniture/{item_id}", response_model=ItemOut, summary="Изменить позицию каталога")
async def put_item(
    item_id: uuid.UUID, body: ItemIn, current: Auth, request: Request, db: DbSession
) -> ItemOut:
    if not _is_manager(current):
        raise Problem(403, "forbidden", "Каталог ведут менеджеры платформы")
    _check_item(body)
    item = await db.get(FurnitureItem, item_id)
    if item is None:
        raise Problem(404, "not-found", "Позиция не найдена")
    old = {"price_rub_kop": item.price_rub_kop, "is_active": item.is_active}
    _apply_item(item, body)
    audit(
        db,
        entity_type="furniture_item",
        entity_id=item.id,
        action="furniture_item.updated",
        actor_id=current.id,
        ip=client_ip(request),
        old=old,
        new={"price_rub_kop": item.price_rub_kop, "is_active": item.is_active},
    )
    await db.commit()
    return _item_out(item)


# ---------- заказы мебели ----------


class LineOut(BaseModel):
    id: uuid.UUID
    item_id: uuid.UUID | None
    title: str
    wish: str | None
    link: str | None
    room: str | None
    qty: int
    estimate_kop: int | None
    price_kop: int | None
    country: str | None
    category: str | None
    image_url: str | None


class EventOut(BaseModel):
    kind: str
    text: str
    created_at: datetime


class OrderOut(BaseModel):
    id: uuid.UUID
    object_id: uuid.UUID
    address: str | None = None
    number: int
    status: str
    customer_note: str | None
    manager: Person | None
    manager_note: str | None
    goods_kop: int | None
    delivery_kop: int | None
    customs_kop: int | None
    service_kop: int | None
    total_kop: int | None
    eta: date | None
    quote_valid_until: date | None
    carrier: str | None
    tracking: str | None
    lines: list[LineOut]
    events: list[EventOut]
    created_at: datetime
    can_edit: bool  # заказчик собирает подборку
    can_manage: bool  # менеджер ведёт заказ


async def _order_out(
    db: DbSession, o: FurnitureOrder, current: CurrentUser, access: ObjectAccess | None
) -> OrderOut:
    lines = list(
        (
            await db.execute(
                select(FurnitureOrderItem)
                .where(FurnitureOrderItem.order_id == o.id)
                .order_by(FurnitureOrderItem.id)
            )
        ).scalars()
    )
    ids = [x.item_id for x in lines if x.item_id]
    items = (
        {
            i.id: i
            for i in (
                await db.execute(select(FurnitureItem).where(FurnitureItem.id.in_(ids)))
            ).scalars()
        }
        if ids
        else {}
    )
    events = (
        await db.execute(
            select(FurnitureEvent)
            .where(FurnitureEvent.order_id == o.id)
            .order_by(FurnitureEvent.created_at, FurnitureEvent.id)
        )
    ).scalars()
    m = await db.get(User, o.manager_id) if o.manager_id else None
    obj = await db.get(RepairObject, o.object_id)
    is_customer = obj is not None and obj.customer_id == current.id
    return OrderOut(
        id=o.id,
        object_id=o.object_id,
        address=obj.address_text if obj else None,
        number=o.number,
        status=o.status,
        customer_note=o.customer_note,
        manager=Person(id=m.id, name=m.full_name, phone=m.phone) if m else None,
        manager_note=o.manager_note,
        goods_kop=o.goods_kop,
        delivery_kop=o.delivery_kop,
        customs_kop=o.customs_kop,
        service_kop=o.service_kop,
        total_kop=o.total_kop,
        eta=o.eta,
        quote_valid_until=o.quote_valid_until,
        carrier=o.carrier,
        tracking=o.tracking,
        lines=[
            LineOut(
                id=x.id,
                item_id=x.item_id,
                title=x.title,
                wish=x.wish,
                link=x.link,
                room=x.room,
                qty=x.qty,
                estimate_kop=x.estimate_kop,
                price_kop=x.price_kop,
                country=items[x.item_id].country if x.item_id in items else None,
                category=items[x.item_id].category if x.item_id in items else None,
                image_url=items[x.item_id].image_url if x.item_id in items else None,
            )
            for x in lines
        ],
        events=[EventOut(kind=e.kind, text=e.text, created_at=e.created_at) for e in events],
        created_at=o.created_at,
        can_edit=(is_customer or (access is not None and access.is_admin)) and o.status == "draft",
        can_manage=_is_manager(current),
    )


async def _order(
    db: DbSession, current: CurrentUser, order_id: uuid.UUID, *, lock: bool = False
) -> tuple[FurnitureOrder, ObjectAccess]:
    o = await db.get(FurnitureOrder, order_id, with_for_update=lock)
    if o is None:
        raise Problem(404, "not-found", "Заказ не найден")
    access = await load_object(db, current, o.object_id)
    return o, access


def _customer_side(access: ObjectAccess) -> bool:
    return access.is_customer or access.is_admin


@router.get(
    "/objects/{object_id}/furniture-orders",
    response_model=list[OrderOut],
    summary="Заказы мебели по объекту",
)
async def object_orders(object_id: uuid.UUID, current: Auth, db: DbSession) -> list[OrderOut]:
    access = await load_object(db, current, object_id)
    orders = (
        await db.execute(
            select(FurnitureOrder)
            .where(FurnitureOrder.object_id == object_id)
            .order_by(FurnitureOrder.number.desc())
        )
    ).scalars()
    return [await _order_out(db, o, current, access) for o in orders]


class LineIn(BaseModel):
    item_id: uuid.UUID | None = None
    # «Найдите мне»: описание и/или ссылка на понравившуюся вещь.
    wish: str | None = Field(default=None, max_length=2000)
    link: str | None = Field(default=None, max_length=500, pattern=r"^https?://")
    room: str | None = Field(default=None, max_length=60)
    qty: int = Field(default=1, ge=1, le=100)


@router.post(
    "/objects/{object_id}/furniture-orders/lines",
    response_model=OrderOut,
    status_code=201,
    summary="Добавить в подборку",
)
async def add_line(
    object_id: uuid.UUID, body: LineIn, current: Auth, request: Request, db: DbSession
) -> OrderOut:
    access = await load_object(db, current, object_id, lock=True)
    if not _customer_side(access):
        raise Problem(403, "forbidden", "Подборку собирает заказчик")
    if access.obj.package != "lux":
        raise Problem(409, "not-lux", "Мебель на заказ — в пакете «Люкс»")
    if body.item_id is None and not (body.wish or body.link):
        raise Problem(422, "empty-line", "Выберите позицию или опишите, что найти")
    item = None
    if body.item_id is not None:
        item = await db.get(FurnitureItem, body.item_id)
        if item is None or not item.is_active:
            raise Problem(404, "not-found", "Позиции нет в каталоге")
    order = (
        await db.execute(
            select(FurnitureOrder)
            .where(FurnitureOrder.object_id == object_id, FurnitureOrder.status == "draft")
            .with_for_update()
        )
    ).scalar_one_or_none()
    if order is None:
        order = FurnitureOrder(
            id=uuid7(),
            object_id=object_id,
            number=await service.next_number(db),
            status="draft",
            manager_id=access.obj.lux_manager_id,
        )
        db.add(order)
        await db.flush()
    line = FurnitureOrderItem(
        id=uuid7(),
        order_id=order.id,
        item_id=item.id if item else None,
        title=item.title if item else "Найти по описанию",
        wish=(body.wish or "").strip() or None,
        link=body.link,
        room=(body.room or "").strip() or None,
        qty=body.qty,
        estimate_kop=item.price_rub_kop if item else None,
    )
    db.add(line)
    audit(
        db,
        entity_type="furniture_order",
        entity_id=order.id,
        action="furniture_order.line_added",
        actor_id=current.id,
        ip=client_ip(request),
        new={"item_id": line.item_id, "qty": line.qty},
    )
    await db.commit()
    return await _order_out(db, order, current, access)


class QtyIn(BaseModel):
    qty: int = Field(ge=0, le=100)  # 0 — убрать


@router.put(
    "/furniture-order-lines/{line_id}", response_model=OrderOut, summary="Количество в подборке"
)
async def set_qty(
    line_id: uuid.UUID, body: QtyIn, current: Auth, request: Request, db: DbSession
) -> OrderOut:
    line = await db.get(FurnitureOrderItem, line_id)
    if line is None:
        raise Problem(404, "not-found", "Позиция не найдена")
    order, access = await _order(db, current, line.order_id, lock=True)
    if not _customer_side(access):
        raise Problem(403, "forbidden", "Подборку меняет заказчик")
    if order.status != "draft":
        raise Problem(409, "order-locked", "Подборка уже у менеджера")
    if body.qty == 0:
        await db.delete(line)
    else:
        line.qty = body.qty
    await db.commit()
    return await _order_out(db, order, current, access)


@router.get(
    "/furniture-orders/{order_id}/estimate",
    response_model=LogisticsEstimate,
    summary="Ориентир доставки и таможни",
)
async def order_estimate(order_id: uuid.UUID, current: Auth, db: DbSession) -> LogisticsEstimate:
    order, _ = await _order(db, current, order_id)
    return await service.estimate(db, order)


class StatusIn(BaseModel):
    status: str
    note: str | None = Field(default=None, max_length=2000)


@router.post(
    "/furniture-orders/{order_id}/status", response_model=OrderOut, summary="Следующий шаг"
)
async def set_status(
    order_id: uuid.UUID, body: StatusIn, current: Auth, request: Request, db: DbSession
) -> OrderOut:
    order, access = await _order(db, current, order_id, lock=True)
    if body.status not in ORDER_STATUSES:
        raise Problem(422, "bad-status", "Неизвестный статус")
    if body.status == "quoted":
        raise Problem(422, "use-quote", "Предложение отправляется отдельной формой")
    if body.status == "requested" and order.status == "draft":
        count = await db.scalar(
            select(FurnitureOrderItem.id).where(FurnitureOrderItem.order_id == order.id).limit(1)
        )
        if not count:
            raise Problem(422, "empty-order", "Подборка пуста")
    if body.status == "confirmed" and service.quote_expired(order):
        raise Problem(409, "quote-expired", "Предложение устарело, попросите менеджера обновить")
    old = order.status
    service.move(
        order,
        body.status,
        by_customer=_customer_side(access),
        by_manager=_is_manager(current),
    )
    obj = access.obj
    if body.status == "requested" and order.manager_id is None:
        order.manager_id = obj.lux_manager_id
    text = f"{ORDER_STATUSES[body.status]}"
    if body.note:
        text += f". {body.note.strip()}"
    service.event(db, order, "status", text, current.id)
    audit(
        db,
        entity_type="furniture_order",
        entity_id=order.id,
        action="furniture_order.status",
        actor_id=current.id,
        ip=client_ip(request),
        old={"status": old},
        new={"status": body.status},
    )
    link = notes.url(f"/objects/{obj.id}/furniture")
    if body.status == "requested":
        to = (
            [order.manager_id]
            if order.manager_id
            else await notes.platform_staff_ids(db, MANAGER_ROLES)
        )
        notes.notify(
            db,
            to,
            "furniture_requested",
            number=order.number,
            address=obj.address_text,
            url=notes.url("/lux"),
        )
    elif body.status == "confirmed":
        to = (
            [order.manager_id]
            if order.manager_id
            else await notes.platform_staff_ids(db, MANAGER_ROLES)
        )
        notes.notify(
            db,
            to,
            "furniture_confirmed",
            number=order.number,
            total=notes.rub(order.total_kop or 0),
            url=notes.url("/lux"),
        )
    elif _is_manager(current) and not access.is_customer:
        notes.notify(
            db,
            [obj.customer_id],
            "furniture_status",
            number=order.number,
            status=ORDER_STATUSES[body.status].lower(),
            url=link,
        )
    await db.commit()
    return await _order_out(db, order, current, access)


class QuoteLine(BaseModel):
    id: uuid.UUID
    title: str | None = Field(default=None, min_length=2, max_length=300)
    price_kop: int = Field(gt=0, le=10**12)


class QuoteIn(BaseModel):
    lines: list[QuoteLine]
    delivery_kop: int = Field(ge=0, le=10**12)
    customs_kop: int = Field(ge=0, le=10**12)
    service_kop: int = Field(ge=0, le=10**12)
    eta: date
    valid_until: date
    note: str | None = Field(default=None, max_length=2000)


@router.put(
    "/furniture-orders/{order_id}/quote", response_model=OrderOut, summary="Предложение менеджера"
)
async def quote(
    order_id: uuid.UUID, body: QuoteIn, current: Auth, request: Request, db: DbSession
) -> OrderOut:
    if not _is_manager(current):
        raise Problem(403, "forbidden", "Предложение готовит менеджер")
    order, access = await _order(db, current, order_id, lock=True)
    if order.status not in ("requested", "quoted"):
        raise Problem(409, "bad-transition", "Предложение готовится по новому заказу")
    lines = {
        x.id: x
        for x in (
            await db.execute(
                select(FurnitureOrderItem).where(FurnitureOrderItem.order_id == order.id)
            )
        ).scalars()
    }
    given = {q.id: q for q in body.lines}
    if set(given) != set(lines):
        raise Problem(422, "lines-mismatch", "Укажите цену для каждой позиции заказа")
    today = utcnow().date()
    if body.valid_until < today or body.eta < today:
        raise Problem(422, "bad-date", "Даты должны быть в будущем")
    goods = 0
    for lid, line in lines.items():
        q = given[lid]
        line.price_kop = q.price_kop
        if q.title:
            line.title = q.title.strip()
        goods += q.price_kop * line.qty
    order.goods_kop = goods
    order.delivery_kop, order.customs_kop = body.delivery_kop, body.customs_kop
    order.service_kop = body.service_kop
    order.total_kop = goods + body.delivery_kop + body.customs_kop + body.service_kop
    order.eta, order.quote_valid_until = body.eta, body.valid_until
    order.manager_note = (body.note or "").strip() or None
    order.manager_id = order.manager_id or current.id
    if order.status == "requested":
        service.move(order, "quoted", by_customer=False, by_manager=True)
    service.event(
        db,
        order,
        "status",
        f"Предложение: {notes.rub(order.total_kop)}, доставка к {body.eta:%d.%m.%Y}",
        current.id,
    )
    audit(
        db,
        entity_type="furniture_order",
        entity_id=order.id,
        action="furniture_order.quoted",
        actor_id=current.id,
        ip=client_ip(request),
        new={"total_kop": order.total_kop},
    )
    obj = access.obj
    notes.notify(
        db,
        [obj.customer_id],
        "furniture_quoted",
        number=order.number,
        total=notes.rub(order.total_kop),
        url=notes.url(f"/objects/{obj.id}/furniture"),
    )
    await db.commit()
    return await _order_out(db, order, current, access)


class ShipIn(BaseModel):
    carrier: str | None = Field(default=None, max_length=100)
    tracking: str | None = Field(default=None, max_length=100)
    text: str | None = Field(default=None, min_length=2, max_length=1000)
    eta: date | None = None


@router.post(
    "/furniture-orders/{order_id}/logistics",
    response_model=OrderOut,
    summary="Отметка о доставке",
)
async def logistics(
    order_id: uuid.UUID, body: ShipIn, current: Auth, request: Request, db: DbSession
) -> OrderOut:
    if not _is_manager(current):
        raise Problem(403, "forbidden", "Доставку ведёт менеджер")
    order, access = await _order(db, current, order_id, lock=True)
    if order.status not in ("paid", "ordered", "shipping", "delivered"):
        raise Problem(409, "bad-transition", "Доставка — после оплаты")
    if body.carrier is not None:
        order.carrier = body.carrier.strip() or None
    if body.tracking is not None:
        order.tracking = body.tracking.strip() or None
    if body.eta is not None and body.eta != order.eta:
        service.event(db, order, "logistics", f"Новый срок: {body.eta:%d.%m.%Y}", current.id)
        order.eta = body.eta
    if body.text:
        service.event(db, order, "logistics", body.text.strip(), current.id)
    audit(
        db,
        entity_type="furniture_order",
        entity_id=order.id,
        action="furniture_order.logistics",
        actor_id=current.id,
        ip=client_ip(request),
        new={"tracking": order.tracking, "eta": body.eta},
    )
    await db.commit()
    return await _order_out(db, order, current, access)


@router.get("/lux/orders", response_model=list[OrderOut], summary="Очередь менеджера")
async def queue(
    current: Auth, db: DbSession, mine: bool = False, done: bool = False
) -> list[OrderOut]:
    if not _is_manager(current):
        raise Problem(403, "forbidden", "Только менеджеры платформы")
    finished = ("installed", "cancelled", "draft")
    stmt = select(FurnitureOrder).order_by(FurnitureOrder.updated_at.desc()).limit(200)
    stmt = stmt.where(
        FurnitureOrder.status.in_(finished[:2]) if done else FurnitureOrder.status.not_in(finished)
    )
    if mine:
        stmt = stmt.where(FurnitureOrder.manager_id == current.id)
    return [await _order_out(db, o, current, None) for o in (await db.execute(stmt)).scalars()]


class LuxObjectOut(BaseModel):
    id: uuid.UUID
    address: str
    status: str
    manager: Person | None


@router.get("/lux/objects", response_model=list[LuxObjectOut], summary="Объекты «Люкс»")
async def lux_objects(current: Auth, db: DbSession) -> list[LuxObjectOut]:
    if not _is_manager(current):
        raise Problem(403, "forbidden", "Только менеджеры платформы")
    rows = (
        await db.execute(
            select(RepairObject, User)
            .outerjoin(User, User.id == RepairObject.lux_manager_id)
            .where(RepairObject.package == "lux", RepairObject.deleted_at.is_(None))
            .order_by(RepairObject.created_at.desc())
            .limit(200)
        )
    ).all()
    return [
        LuxObjectOut(
            id=o.id,
            address=o.address_text,
            status=o.status.value,
            manager=Person(id=u.id, name=u.full_name, phone=u.phone) if u else None,
        )
        for o, u in rows
    ]
