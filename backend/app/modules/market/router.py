"""Маркетплейс подрядчиков — второй режим поиска подрядчика рядом с подбором платформой.

* Каталог: проверенные строительные компании и бригады с профилем, портфолио и оценками.
* Заказчик выбирает режим на объекте: «подберёт платформа» или «выберу сам из откликов».
* В режиме маркетплейса заказчик публикует заявку (без точного адреса), подрядчики
  откликаются с ценой, сроком и датой начала, заказчик выбирает отклик — подрядчик
  назначается на объект так же, как при подборе платформой.
* После завершения объекта заказчик ставит оценку — из неё складывается рейтинг.
"""

import asyncio
import uuid
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import array as pg_array

from app.core.access import load_object
from app.core.audit import audit
from app.core.config import get_settings
from app.core.deps import Auth, CurrentUser, DbSession, client_ip
from app.core.ids import uuid7
from app.core.problems import Problem
from app.core.security import utcnow
from app.core.storage import get_storage, signed_file_url
from app.domain.stage_types import STAGE_TYPES
from app.models import (
    ContractorProfile,
    ContractorReview,
    MarketBid,
    MarketTask,
    Organization,
    OrgMember,
    PortfolioPhoto,
    RepairObject,
)
from app.models.enums import ObjectStatus, OrgRole, OrgType
from app.modules.field.antifraud import NotAnImage, inspect_image
from app.modules.notifications import service as notes

router = APIRouter(tags=["market"])

MODES = ("platform", "market")
OPEN_STATUSES = (
    ObjectStatus.draft,
    ObjectStatus.survey,
    ObjectStatus.estimating,
    ObjectStatus.contracting,
)
BIDDER_ROLES = {OrgRole.contractor_admin, OrgRole.estimator}
MAX_PORTFOLIO = 30


def _verified_contractors(current: CurrentUser, roles: set[OrgRole]) -> list[Organization]:
    return [
        org
        for member, org in current.memberships
        if org.type == OrgType.contractor
        and org.verified_at is not None
        and org.deleted_at is None
        and member.role in roles
    ]


async def _contractor(db: DbSession, org_id: uuid.UUID) -> Organization:
    org = await db.get(Organization, org_id)
    if org is None or org.deleted_at is not None or org.type != OrgType.contractor:
        raise Problem(404, "not-found", "Подрядчик не найден")
    return org


# ---------- каталог и профиль ----------


class ContractorCard(BaseModel):
    org_id: uuid.UUID
    name: str
    legal_name: str
    kind: str
    city: str | None
    specializations: list[str]
    team_size: int | None
    since_year: int | None
    verified: bool
    accepts_tasks: bool
    rating: float | None
    reviews: int
    objects_done: int
    objects_in_work: int
    cover_url: str | None
    vip: bool = False


class PortfolioOut(BaseModel):
    id: uuid.UUID
    url: str
    caption: str | None


class ReviewOut(BaseModel):
    rating: int
    text: str | None
    created_at: datetime


class ContractorOut(ContractorCard):
    about: str | None
    portfolio: list[PortfolioOut]
    review_list: list[ReviewOut]
    can_edit: bool


async def _stats(
    db: DbSession, org_ids: list[uuid.UUID]
) -> tuple[dict[uuid.UUID, tuple[float, int]], dict[uuid.UUID, tuple[int, int]]]:
    if not org_ids:
        return {}, {}
    ratings = {
        o: (float(avg), n)
        for o, avg, n in (
            await db.execute(
                select(
                    ContractorReview.org_id,
                    func.avg(ContractorReview.rating),
                    func.count(),
                )
                .where(ContractorReview.org_id.in_(org_ids))
                .group_by(ContractorReview.org_id)
            )
        ).all()
    }
    done = func.count().filter(RepairObject.status == ObjectStatus.completed)
    work = func.count().filter(RepairObject.status.in_([ObjectStatus.active, ObjectStatus.dispute]))
    objects = {
        o: (d, w)
        for o, d, w in (
            await db.execute(
                select(RepairObject.contractor_org_id, done, work)
                .where(
                    RepairObject.contractor_org_id.in_(org_ids),
                    RepairObject.deleted_at.is_(None),
                )
                .group_by(RepairObject.contractor_org_id)
            )
        ).all()
    }
    return ratings, objects


async def _cards(
    db: DbSession, orgs: list[Organization], viewer: uuid.UUID
) -> list[ContractorCard]:
    ids = [o.id for o in orgs]
    profiles = {
        p.org_id: p
        for p in (
            await db.execute(select(ContractorProfile).where(ContractorProfile.org_id.in_(ids)))
        ).scalars()
    }
    covers: dict[uuid.UUID, uuid.UUID] = {}
    for org_id, photo_id in (
        await db.execute(
            select(PortfolioPhoto.org_id, PortfolioPhoto.id)
            .where(PortfolioPhoto.org_id.in_(ids))
            .order_by(PortfolioPhoto.created_at)
        )
    ).all():
        covers.setdefault(org_id, photo_id)
    ratings, objects = await _stats(db, ids)
    out = []
    for org in orgs:
        p = profiles.get(org.id)
        r = ratings.get(org.id)
        d, w = objects.get(org.id, (0, 0))
        cover = covers.get(org.id)
        out.append(
            ContractorCard(
                org_id=org.id,
                name=(p.display_name if p else None) or org.legal_name,
                legal_name=org.legal_name,
                kind=p.kind if p else "company",
                city=p.city if p else None,
                specializations=p.specializations if p else [],
                team_size=p.team_size if p else None,
                since_year=p.since_year if p else None,
                verified=org.verified_at is not None,
                accepts_tasks=p.accepts_tasks if p else True,
                rating=round(r[0], 2) if r else None,
                reviews=r[1] if r else 0,
                objects_done=d,
                objects_in_work=w,
                cover_url=signed_file_url("portfolio", cover, viewer) if cover else None,
                vip=org.vip_at is not None,
            )
        )
    return out


@router.get("/contractors", response_model=list[ContractorCard], summary="Каталог подрядчиков")
async def catalog(
    current: Auth,
    db: DbSession,
    work_type: str | None = None,
    kind: Literal["company", "brigade"] | None = None,
    q: str | None = Query(default=None, max_length=100),
    vip: bool = False,
) -> list[ContractorCard]:
    stmt = (
        select(Organization)
        .outerjoin(ContractorProfile, ContractorProfile.org_id == Organization.id)
        .where(
            Organization.type == OrgType.contractor,
            Organization.deleted_at.is_(None),
            Organization.verified_at.is_not(None),
        )
        .order_by(Organization.legal_name)
        .limit(200)
    )
    if work_type:
        stmt = stmt.where(ContractorProfile.specializations.contains([work_type]))
    if kind == "brigade":
        stmt = stmt.where(ContractorProfile.kind == "brigade")
    elif kind == "company":
        stmt = stmt.where((ContractorProfile.kind == "company") | ContractorProfile.id.is_(None))
    if vip:
        stmt = stmt.where(Organization.vip_at.is_not(None))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            Organization.legal_name.ilike(like)
            | ContractorProfile.display_name.ilike(like)
            | ContractorProfile.city.ilike(like)
        )
    orgs = list((await db.execute(stmt)).scalars())
    cards = await _cards(db, orgs, current.id)
    # Сначала с оценками и опытом на платформе.
    cards.sort(key=lambda c: (not c.vip, -(c.rating or 0), -c.objects_done, c.name))
    return cards


def _can_edit_profile(current: CurrentUser, org_id: uuid.UUID) -> bool:
    return current.is_platform_admin or OrgRole.contractor_admin in current.roles_in_org(org_id)


async def _contractor_out(db: DbSession, org: Organization, current: CurrentUser) -> ContractorOut:
    can_edit = _can_edit_profile(current, org.id)
    if org.verified_at is None and not can_edit:
        raise Problem(404, "not-found", "Подрядчик не найден")
    card = (await _cards(db, [org], current.id))[0]
    profile = (
        await db.execute(select(ContractorProfile).where(ContractorProfile.org_id == org.id))
    ).scalar_one_or_none()
    photos = (
        await db.execute(
            select(PortfolioPhoto)
            .where(PortfolioPhoto.org_id == org.id)
            .order_by(PortfolioPhoto.created_at)
        )
    ).scalars()
    reviews = (
        await db.execute(
            select(ContractorReview)
            .where(ContractorReview.org_id == org.id)
            .order_by(ContractorReview.created_at.desc())
            .limit(50)
        )
    ).scalars()
    return ContractorOut(
        **card.model_dump(),
        about=profile.about if profile else None,
        portfolio=[
            PortfolioOut(
                id=p.id, url=signed_file_url("portfolio", p.id, current.id), caption=p.caption
            )
            for p in photos
        ],
        review_list=[
            ReviewOut(rating=r.rating, text=r.text, created_at=r.created_at) for r in reviews
        ],
        can_edit=can_edit,
    )


@router.get("/contractors/{org_id}", response_model=ContractorOut, summary="Профиль подрядчика")
async def contractor(org_id: uuid.UUID, current: Auth, db: DbSession) -> ContractorOut:
    return await _contractor_out(db, await _contractor(db, org_id), current)


class ProfileIn(BaseModel):
    kind: Literal["company", "brigade"] = "company"
    display_name: str = Field(min_length=2, max_length=200)
    about: str | None = Field(default=None, max_length=4000)
    city: str | None = Field(default=None, max_length=100)
    specializations: list[str] = Field(default_factory=list, max_length=20)
    team_size: int | None = Field(default=None, ge=1, le=1000)
    since_year: int | None = Field(default=None, ge=1950, le=2100)
    accepts_tasks: bool = True


@router.put(
    "/contractors/{org_id}/profile", response_model=ContractorOut, summary="Изменить профиль"
)
async def put_profile(
    org_id: uuid.UUID, body: ProfileIn, current: Auth, request: Request, db: DbSession
) -> ContractorOut:
    org = await _contractor(db, org_id)
    if not _can_edit_profile(current, org.id):
        raise Problem(403, "forbidden", "Профиль ведёт руководитель подрядчика")
    unknown = [s for s in body.specializations if s not in STAGE_TYPES]
    if unknown:
        raise Problem(422, "unknown-work-type", f"Неизвестный вид работ: {unknown[0]}")
    profile = (
        await db.execute(select(ContractorProfile).where(ContractorProfile.org_id == org.id))
    ).scalar_one_or_none()
    if profile is None:
        profile = ContractorProfile(org_id=org.id, display_name=body.display_name)
        db.add(profile)
    data = body.model_dump()
    data["specializations"] = list(dict.fromkeys(body.specializations))
    for k, v in data.items():
        setattr(profile, k, v.strip() if isinstance(v, str) else v)
    audit(
        db,
        entity_type="organization",
        entity_id=org.id,
        action="contractor.profile_updated",
        actor_id=current.id,
        ip=client_ip(request),
        new={"display_name": body.display_name, "kind": body.kind},
    )
    await db.commit()
    return await _contractor_out(db, org, current)


@router.post(
    "/contractors/{org_id}/portfolio",
    response_model=ContractorOut,
    status_code=201,
    summary="Добавить фото в портфолио",
)
async def add_photo(
    org_id: uuid.UUID,
    current: Auth,
    request: Request,
    db: DbSession,
    photo: UploadFile = File(...),
    caption: str | None = Form(default=None, max_length=200),
) -> ContractorOut:
    org = await _contractor(db, org_id)
    if not _can_edit_profile(current, org.id):
        raise Problem(403, "forbidden", "Портфолио ведёт руководитель подрядчика")
    count = await db.scalar(
        select(func.count()).select_from(PortfolioPhoto).where(PortfolioPhoto.org_id == org.id)
    )
    if (count or 0) >= MAX_PORTFOLIO:
        raise Problem(409, "portfolio-full", f"В портфолио не больше {MAX_PORTFOLIO} фото")
    s = get_settings()
    data = await photo.read(s.max_photo_bytes + 1)
    if len(data) > s.max_photo_bytes:
        raise Problem(413, "file-too-large", "Фото слишком большое")
    try:
        info = await asyncio.to_thread(inspect_image, data)
    except NotAnImage as exc:
        raise Problem(422, "not-an-image", str(exc)) from None
    pid = uuid7()
    key = f"portfolio/{org.id}/{pid}.{info.ext}"
    await get_storage().put(key, data, info.content_type)
    db.add(
        PortfolioPhoto(
            id=pid,
            org_id=org.id,
            storage_key=key,
            content_type=info.content_type,
            caption=caption.strip() if caption else None,
        )
    )
    audit(
        db,
        entity_type="organization",
        entity_id=org.id,
        action="contractor.portfolio_added",
        actor_id=current.id,
        ip=client_ip(request),
    )
    await db.commit()
    return await _contractor_out(db, org, current)


@router.delete(
    "/contractors/{org_id}/portfolio/{photo_id}",
    response_model=ContractorOut,
    summary="Убрать фото из портфолио",
)
async def delete_photo(
    org_id: uuid.UUID, photo_id: uuid.UUID, current: Auth, db: DbSession
) -> ContractorOut:
    org = await _contractor(db, org_id)
    if not _can_edit_profile(current, org.id):
        raise Problem(403, "forbidden", "Портфолио ведёт руководитель подрядчика")
    photo = await db.get(PortfolioPhoto, photo_id)
    if photo is None or photo.org_id != org.id:
        raise Problem(404, "not-found", "Фото не найдено")
    await db.delete(photo)
    await db.commit()
    await get_storage().delete(photo.storage_key)
    return await _contractor_out(db, org, current)


# ---------- режим подбора на объекте ----------


class ModeIn(BaseModel):
    mode: Literal["platform", "market"]


@router.put("/objects/{object_id}/match-mode", summary="Как искать подрядчика")
async def set_mode(
    object_id: uuid.UUID, body: ModeIn, current: Auth, request: Request, db: DbSession
) -> dict[str, str]:
    access = await load_object(db, current, object_id, lock=True)
    if not (access.is_customer or access.is_admin):
        raise Problem(403, "forbidden", "Способ подбора выбирает заказчик")
    obj = access.obj
    if obj.contractor_org_id is not None or obj.status not in OPEN_STATUSES:
        raise Problem(409, "contractor-chosen", "Подрядчик уже выбран")
    if obj.match_mode != body.mode:
        task = await _task_of(db, obj.id)
        if task is not None and task.status == "open":
            task.status = "closed"
        audit(
            db,
            entity_type="object",
            entity_id=obj.id,
            action="object.match_mode_set",
            actor_id=current.id,
            ip=client_ip(request),
            old={"mode": obj.match_mode},
            new={"mode": body.mode},
        )
        obj.match_mode = body.mode
        await db.commit()
    return {"mode": obj.match_mode}


# ---------- заявка и отклики ----------


class TaskIn(BaseModel):
    title: str = Field(min_length=5, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    area_hint: str = Field(min_length=2, max_length=100)
    work_types: list[str] = Field(default_factory=list, max_length=20)
    budget_kop: int | None = Field(default=None, gt=0, le=10**12)
    desired_start: date | None = None
    bids_until: date | None = None


class BidOut(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    contractor: ContractorCard | None
    price_kop: int
    days: int
    start_date: date | None
    message: str | None
    status: str
    created_at: datetime


class TaskOut(BaseModel):
    id: uuid.UUID
    object_id: uuid.UUID
    title: str
    description: str | None
    area_hint: str
    area_m2: float | None
    work_types: list[str]
    budget_kop: int | None
    desired_start: date | None
    bids_until: date | None
    status: str
    created_at: datetime
    bids_count: int
    # Заказчику и администратору — все отклики, подрядчику — только свой.
    bids: list[BidOut]
    my_bid: BidOut | None = None
    # Пакет «Люкс»: откликаются только VIP-подрядчики.
    lux: bool = False


async def _task_of(db: DbSession, object_id: uuid.UUID) -> MarketTask | None:
    return (
        await db.execute(select(MarketTask).where(MarketTask.object_id == object_id))
    ).scalar_one_or_none()


async def _bid_outs(
    db: DbSession, bids: list[MarketBid], viewer: uuid.UUID, with_cards: bool
) -> list[BidOut]:
    cards: dict[uuid.UUID, ContractorCard] = {}
    if with_cards and bids:
        orgs = list(
            (
                await db.execute(
                    select(Organization).where(Organization.id.in_({b.org_id for b in bids}))
                )
            ).scalars()
        )
        cards = {c.org_id: c for c in await _cards(db, orgs, viewer)}
    return [
        BidOut(
            id=b.id,
            org_id=b.org_id,
            contractor=cards.get(b.org_id),
            price_kop=b.price_kop,
            days=b.days,
            start_date=b.start_date,
            message=b.message,
            status=b.status,
            created_at=b.created_at,
        )
        for b in bids
    ]


async def _task_out(db: DbSession, task: MarketTask, current: CurrentUser, owner: bool) -> TaskOut:
    obj = await db.get(RepairObject, task.object_id)
    bids = list(
        (
            await db.execute(
                select(MarketBid)
                .where(MarketBid.task_id == task.id)
                .order_by(MarketBid.price_kop, MarketBid.created_at)
            )
        ).scalars()
    )
    mine_orgs = {o.id for o in _verified_contractors(current, BIDDER_ROLES)}
    mine = next((b for b in bids if b.org_id in mine_orgs), None)
    my_out = (await _bid_outs(db, [mine], current.id, False))[0] if mine else None
    return TaskOut(
        id=task.id,
        object_id=task.object_id,
        title=task.title,
        description=task.description,
        area_hint=task.area_hint,
        area_m2=float(obj.area_m2) if obj and obj.area_m2 is not None else None,
        work_types=task.work_types,
        budget_kop=task.budget_kop,
        desired_start=task.desired_start,
        bids_until=task.bids_until,
        status=task.status,
        created_at=task.created_at,
        bids_count=sum(1 for b in bids if b.status != "withdrawn"),
        bids=await _bid_outs(db, bids, current.id, True) if owner else [],
        my_bid=my_out,
        lux=bool(obj and obj.package == "lux"),
    )


@router.get(
    "/objects/{object_id}/market-task",
    response_model=TaskOut | None,
    summary="Заявка объекта и отклики",
)
async def object_task(object_id: uuid.UUID, current: Auth, db: DbSession) -> TaskOut | None:
    access = await load_object(db, current, object_id)
    task = await _task_of(db, object_id)
    if task is None:
        return None
    return await _task_out(db, task, current, access.is_customer or access.is_admin)


@router.put(
    "/objects/{object_id}/market-task", response_model=TaskOut, summary="Опубликовать заявку"
)
async def put_task(
    object_id: uuid.UUID, body: TaskIn, current: Auth, request: Request, db: DbSession
) -> TaskOut:
    access = await load_object(db, current, object_id, lock=True)
    if not (access.is_customer or access.is_admin):
        raise Problem(403, "forbidden", "Заявку публикует заказчик")
    obj = access.obj
    if obj.match_mode != "market":
        raise Problem(409, "not-market-mode", "Сначала выберите «Выберу подрядчика сам»")
    if obj.contractor_org_id is not None or obj.status not in OPEN_STATUSES:
        raise Problem(409, "contractor-chosen", "Подрядчик уже выбран")
    unknown = [s for s in body.work_types if s not in STAGE_TYPES]
    if unknown:
        raise Problem(422, "unknown-work-type", f"Неизвестный вид работ: {unknown[0]}")
    task = await _task_of(db, obj.id)
    first = task is None or task.status != "open"
    if task is None:
        task = MarketTask(id=uuid7(), object_id=obj.id, title=body.title, area_hint=body.area_hint)
        db.add(task)
    for k, v in body.model_dump().items():
        setattr(task, k, v.strip() if isinstance(v, str) else v)
    task.status = "open"
    await db.flush()
    audit(
        db,
        entity_type="market_task",
        entity_id=task.id,
        action="market.task_published" if first else "market.task_updated",
        actor_id=current.id,
        ip=client_ip(request),
        new={"title": task.title, "budget_kop": task.budget_kop},
    )
    if first:
        notes.notify(
            db,
            await _bidder_ids(db, body.work_types, vip_only=obj.package == "lux"),
            "market_task",
            area=task.area_hint,
            url=notes.url("/market"),
        )
    await db.commit()
    return await _task_out(db, task, current, True)


async def _bidder_ids(
    db: DbSession, work_types: list[str], vip_only: bool = False
) -> list[uuid.UUID]:
    """Руководители проверенных подрядчиков, которые берут заявки и делают такие работы."""
    stmt = (
        select(OrgMember.user_id)
        .join(Organization, Organization.id == OrgMember.org_id)
        .outerjoin(ContractorProfile, ContractorProfile.org_id == Organization.id)
        .where(
            Organization.type == OrgType.contractor,
            Organization.verified_at.is_not(None),
            Organization.deleted_at.is_(None),
            OrgMember.role == OrgRole.contractor_admin,
            (ContractorProfile.id.is_(None)) | ContractorProfile.accepts_tasks.is_(True),
        )
    )
    if vip_only:
        stmt = stmt.where(Organization.vip_at.is_not(None))
    if work_types:
        # Без профиля — шлём всем; с профилем — если есть хотя бы один нужный вид работ.
        stmt = stmt.where(
            ContractorProfile.id.is_(None)
            | (func.jsonb_array_length(ContractorProfile.specializations) == 0)
            | ContractorProfile.specializations.has_any(pg_array(work_types))
        )
    return list(dict.fromkeys((await db.execute(stmt)).scalars()))


@router.post("/market-tasks/{task_id}/close", response_model=TaskOut, summary="Снять заявку")
async def close_task(task_id: uuid.UUID, current: Auth, request: Request, db: DbSession) -> TaskOut:
    task = await db.get(MarketTask, task_id, with_for_update=True)
    if task is None:
        raise Problem(404, "not-found", "Заявка не найдена")
    access = await load_object(db, current, task.object_id)
    if not (access.is_customer or access.is_admin):
        raise Problem(403, "forbidden", "Заявку снимает заказчик")
    if task.status == "open":
        task.status = "closed"
        audit(
            db,
            entity_type="market_task",
            entity_id=task.id,
            action="market.task_closed",
            actor_id=current.id,
            ip=client_ip(request),
        )
        await db.commit()
    return await _task_out(db, task, current, True)


@router.get("/market/tasks", response_model=list[TaskOut], summary="Открытые заявки")
async def open_tasks(current: Auth, db: DbSession) -> list[TaskOut]:
    if not (current.is_platform_admin or _verified_contractors(current, BIDDER_ROLES)):
        raise Problem(
            403,
            "forbidden",
            "Заявки видят проверенные подрядчики",
            "Пройдите проверку организации у администратора платформы.",
        )
    stmt = (
        select(MarketTask)
        .join(RepairObject, RepairObject.id == MarketTask.object_id)
        .where(MarketTask.status == "open")
        .order_by(MarketTask.created_at.desc())
        .limit(100)
    )
    vip = any(o.vip_at for o in _verified_contractors(current, BIDDER_ROLES))
    if not (current.is_platform_admin or vip):
        stmt = stmt.where(RepairObject.package != "lux")
    tasks = (await db.execute(stmt)).scalars()
    return [await _task_out(db, t, current, False) for t in tasks]


class BidIn(BaseModel):
    org_id: uuid.UUID | None = None
    price_kop: int = Field(gt=0, le=10**12)
    days: int = Field(ge=1, le=730)
    start_date: date | None = None
    message: str | None = Field(default=None, max_length=2000)


@router.post(
    "/market-tasks/{task_id}/bids", response_model=TaskOut, status_code=201, summary="Откликнуться"
)
async def bid(
    task_id: uuid.UUID, body: BidIn, current: Auth, request: Request, db: DbSession
) -> TaskOut:
    orgs = _verified_contractors(current, BIDDER_ROLES)
    if body.org_id is not None:
        orgs = [o for o in orgs if o.id == body.org_id]
    if not orgs:
        raise Problem(403, "forbidden", "Откликаются проверенные подрядчики")
    if len(orgs) > 1:
        raise Problem(422, "org-required", "Укажите, от какой организации отклик")
    org = orgs[0]
    task = await db.get(MarketTask, task_id, with_for_update=True)
    if task is None:
        raise Problem(404, "not-found", "Заявка не найдена")
    if task.status != "open":
        raise Problem(409, "task-closed", "Заявка уже закрыта")
    task_obj = await db.get(RepairObject, task.object_id)
    if task_obj is not None and task_obj.package == "lux" and org.vip_at is None:
        raise Problem(403, "not-vip", "На ремонт «Люкс» откликаются VIP-подрядчики")
    existing = (
        await db.execute(
            select(MarketBid).where(MarketBid.task_id == task.id, MarketBid.org_id == org.id)
        )
    ).scalar_one_or_none()
    if existing is None:
        existing = MarketBid(id=uuid7(), task_id=task.id, org_id=org.id, author_id=current.id)
        db.add(existing)
        first = True
    else:
        first = existing.status == "withdrawn"
    existing.price_kop, existing.days = body.price_kop, body.days
    existing.start_date, existing.message = body.start_date, (body.message or "").strip() or None
    existing.status, existing.author_id = "submitted", current.id
    await db.flush()
    audit(
        db,
        entity_type="market_bid",
        entity_id=existing.id,
        action="market.bid_submitted" if first else "market.bid_updated",
        actor_id=current.id,
        ip=client_ip(request),
        new={"price_kop": body.price_kop, "days": body.days},
    )
    if first:
        obj = await db.get(RepairObject, task.object_id)
        if obj is not None:
            notes.notify(
                db,
                [obj.customer_id],
                "market_bid",
                address=obj.address_text,
                price=notes.rub(body.price_kop),
                url=notes.url(f"/objects/{obj.id}#market"),
            )
    await db.commit()
    return await _task_out(db, task, current, False)


@router.post("/market-bids/{bid_id}/withdraw", response_model=TaskOut, summary="Отозвать отклик")
async def withdraw(bid_id: uuid.UUID, current: Auth, request: Request, db: DbSession) -> TaskOut:
    b = await db.get(MarketBid, bid_id, with_for_update=True)
    if b is None or not (BIDDER_ROLES & current.roles_in_org(b.org_id)):
        raise Problem(404, "not-found", "Отклик не найден")
    if b.status != "submitted":
        raise Problem(409, "bid-final", "Отклик уже рассмотрен")
    b.status = "withdrawn"
    audit(
        db,
        entity_type="market_bid",
        entity_id=b.id,
        action="market.bid_withdrawn",
        actor_id=current.id,
        ip=client_ip(request),
    )
    await db.commit()
    task = await db.get(MarketTask, b.task_id)
    assert task is not None
    return await _task_out(db, task, current, False)


@router.post("/market-bids/{bid_id}/accept", response_model=TaskOut, summary="Выбрать подрядчика")
async def accept(bid_id: uuid.UUID, current: Auth, request: Request, db: DbSession) -> TaskOut:
    b = await db.get(MarketBid, bid_id, with_for_update=True)
    if b is None:
        raise Problem(404, "not-found", "Отклик не найден")
    task = await db.get(MarketTask, b.task_id, with_for_update=True)
    assert task is not None
    access = await load_object(db, current, task.object_id, lock=True)
    if not (access.is_customer or access.is_admin):
        raise Problem(403, "forbidden", "Подрядчика выбирает заказчик")
    obj = access.obj
    if task.status != "open" or b.status != "submitted":
        raise Problem(409, "task-closed", "Заявка или отклик уже неактуальны")
    if obj.contractor_org_id is not None or obj.status not in OPEN_STATUSES:
        raise Problem(409, "contractor-chosen", "Подрядчик уже выбран")
    org = await _contractor(db, b.org_id)
    if org.verified_at is None:
        raise Problem(409, "org-not-verified", "Подрядчик ещё не прошёл проверку")
    if obj.package == "lux" and org.vip_at is None:
        raise Problem(409, "contractor-not-vip", "Для «Люкс» выберите VIP-подрядчика")
    now = utcnow()
    others = list(
        (
            await db.execute(
                select(MarketBid).where(
                    MarketBid.task_id == task.id,
                    MarketBid.id != b.id,
                    MarketBid.status == "submitted",
                )
            )
        ).scalars()
    )
    for o in others:
        o.status = "rejected"
    b.status = "accepted"
    task.status, task.awarded_bid_id, task.awarded_at = "awarded", b.id, now
    obj.contractor_org_id = org.id
    ip = client_ip(request)
    audit(
        db,
        entity_type="object",
        entity_id=obj.id,
        action="object.contractor_set",
        actor_id=current.id,
        ip=ip,
        old={"contractor_org_id": None},
        new={"contractor_org_id": org.id, "via": "market", "bid_id": b.id},
    )
    audit(
        db,
        entity_type="market_bid",
        entity_id=b.id,
        action="market.bid_accepted",
        actor_id=current.id,
        ip=ip,
        new={"price_kop": b.price_kop, "days": b.days},
    )
    notes.notify(
        db,
        await notes.org_member_ids(db, org.id, BIDDER_ROLES),
        "market_won",
        address=obj.address_text,
        url=notes.url(f"/objects/{obj.id}"),
    )
    for o in others:
        notes.notify(
            db,
            await notes.org_member_ids(db, o.org_id, BIDDER_ROLES),
            "market_lost",
            title=task.title,
            url=notes.url("/market"),
        )
    await db.commit()
    return await _task_out(db, task, current, True)


# ---------- отзывы ----------


class ReviewIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    text: str | None = Field(default=None, max_length=2000)


@router.post(
    "/objects/{object_id}/contractor-review",
    response_model=ReviewOut,
    status_code=201,
    summary="Оценить подрядчика",
)
async def review(
    object_id: uuid.UUID, body: ReviewIn, current: Auth, request: Request, db: DbSession
) -> ReviewOut:
    access = await load_object(db, current, object_id)
    if not access.is_customer:
        raise Problem(403, "forbidden", "Оценку ставит заказчик")
    obj = access.obj
    if obj.contractor_org_id is None or obj.status != ObjectStatus.completed:
        raise Problem(409, "object-not-completed", "Оценить можно после завершения ремонта")
    exists = await db.scalar(
        select(func.count())
        .select_from(ContractorReview)
        .where(ContractorReview.object_id == obj.id, ContractorReview.author_id == current.id)
    )
    if exists:
        raise Problem(409, "already-reviewed", "Вы уже оценили этот ремонт")
    r = ContractorReview(
        id=uuid7(),
        org_id=obj.contractor_org_id,
        object_id=obj.id,
        author_id=current.id,
        rating=body.rating,
        text=(body.text or "").strip() or None,
    )
    db.add(r)
    await db.flush()
    avg = await db.scalar(
        select(func.avg(ContractorReview.rating)).where(
            ContractorReview.org_id == obj.contractor_org_id
        )
    )
    org = await db.get(Organization, obj.contractor_org_id)
    if org is not None and avg is not None:
        org.rating = round(avg, 2)
    audit(
        db,
        entity_type="object",
        entity_id=obj.id,
        action="object.contractor_reviewed",
        actor_id=current.id,
        ip=client_ip(request),
        new={"rating": body.rating},
    )
    await db.commit()
    await db.refresh(r)
    return ReviewOut(rating=r.rating, text=r.text, created_at=r.created_at)


@router.get(
    "/objects/{object_id}/contractor-review",
    response_model=ReviewOut | None,
    summary="Моя оценка подрядчика",
)
async def my_review(object_id: uuid.UUID, current: Auth, db: DbSession) -> ReviewOut | None:
    await load_object(db, current, object_id)
    r = (
        await db.execute(
            select(ContractorReview).where(
                ContractorReview.object_id == object_id,
                ContractorReview.author_id == current.id,
            )
        )
    ).scalar_one_or_none()
    return ReviewOut(rating=r.rating, text=r.text, created_at=r.created_at) if r else None
