"""«Ремонт с душой»: дизайнеры интерьера на платформе и их работа над проектом объекта.

Сервис подбирает отделку из каталога, а финальное слово — за живым дизайнером: он правит
отделку, пишет концепцию и заметки по комнатам, наполняет комнаты предметами и сдаёт проект.
Утверждённый дизайн дальше меняется только через доработку у дизайнера.
"""

import asyncio
import uuid
from datetime import datetime

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.core.access import ObjectAccess, load_object
from app.core.audit import audit
from app.core.config import get_settings
from app.core.deps import Auth, CurrentUser, DbSession, client_ip
from app.core.ids import uuid7
from app.core.problems import Problem
from app.core.security import utcnow
from app.core.storage import get_storage, signed_file_url
from app.domain.design import STYLE_KEYS
from app.models import (
    DesignDecor,
    DesignerContract,
    DesignerPhoto,
    DesignerProfile,
    DesignProject,
    FurnitureItem,
    ObjectAssignment,
    RepairObject,
    User,
)
from app.models.enums import AssignmentRole
from app.modules.field.antifraud import NotAnImage, inspect_image
from app.modules.notifications import service as notes
from app.modules.objects.router import ensure_assignment

router = APIRouter(tags=["soul"])

MAX_PHOTOS = 30
ACTIVE = ("invited", "offered", "working", "submitted")


# ---------- дизайнеры ----------


class PhotoOut(BaseModel):
    id: uuid.UUID
    url: str
    caption: str | None


class DesignerOut(BaseModel):
    user_id: uuid.UUID
    display_name: str
    about: str | None
    city: str | None
    styles: list[str]
    price_m2_kop: int | None
    since_year: int | None
    accepts_projects: bool
    verified: bool
    projects_done: int
    cover_url: str | None
    photos: list[PhotoOut] = []
    phone: str | None = None  # виден заказчику, когда дизайнер работает над его проектом
    can_edit: bool = False


async def _designer_out(
    db: DbSession,
    p: DesignerProfile,
    current: CurrentUser,
    *,
    full: bool = False,
    phone: str | None = None,
) -> DesignerOut:
    photos = list(
        (
            await db.execute(
                select(DesignerPhoto)
                .where(DesignerPhoto.profile_id == p.id)
                .order_by(DesignerPhoto.created_at)
            )
        ).scalars()
    )
    done = await db.scalar(
        select(func.count())
        .select_from(DesignerContract)
        .where(DesignerContract.designer_id == p.user_id, DesignerContract.status == "approved")
    )
    return DesignerOut(
        user_id=p.user_id,
        display_name=p.display_name,
        about=p.about,
        city=p.city,
        styles=p.styles,
        price_m2_kop=p.price_m2_kop,
        since_year=p.since_year,
        accepts_projects=p.accepts_projects,
        verified=p.verified_at is not None,
        projects_done=done or 0,
        cover_url=signed_file_url("designer", photos[0].id, current.id) if photos else None,
        photos=[
            PhotoOut(id=x.id, url=signed_file_url("designer", x.id, current.id), caption=x.caption)
            for x in photos
        ]
        if full
        else [],
        phone=phone,
        can_edit=p.user_id == current.id or current.is_platform_admin,
    )


async def _profile(db: DbSession, user_id: uuid.UUID) -> DesignerProfile | None:
    return (
        await db.execute(select(DesignerProfile).where(DesignerProfile.user_id == user_id))
    ).scalar_one_or_none()


@router.get("/designers", response_model=list[DesignerOut], summary="Дизайнеры интерьера")
async def designers(
    current: Auth,
    db: DbSession,
    style: str | None = None,
    q: str | None = Query(default=None, max_length=100),
    all: bool = False,
) -> list[DesignerOut]:
    stmt = select(DesignerProfile).order_by(DesignerProfile.display_name).limit(200)
    if not (all and current.is_platform_admin):
        stmt = stmt.where(
            DesignerProfile.verified_at.is_not(None), DesignerProfile.accepts_projects.is_(True)
        )
    if style:
        stmt = stmt.where(DesignerProfile.styles.contains([style]))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            DesignerProfile.display_name.ilike(like) | DesignerProfile.city.ilike(like)
        )
    out = [await _designer_out(db, p, current) for p in (await db.execute(stmt)).scalars()]
    out.sort(key=lambda d: -d.projects_done)
    return out


@router.get("/designers/{user_id}", response_model=DesignerOut, summary="Профиль дизайнера")
async def designer(user_id: uuid.UUID, current: Auth, db: DbSession) -> DesignerOut:
    p = await _profile(db, user_id)
    if p is None or (
        p.verified_at is None and p.user_id != current.id and not current.is_platform_admin
    ):
        raise Problem(404, "not-found", "Дизайнер не найден")
    return await _designer_out(db, p, current, full=True)


class ProfileIn(BaseModel):
    display_name: str = Field(min_length=2, max_length=200)
    about: str | None = Field(default=None, max_length=5000)
    city: str | None = Field(default=None, max_length=100)
    styles: list[str] = Field(default_factory=list)
    price_m2_kop: int | None = Field(default=None, gt=0, le=10**9)
    since_year: int | None = Field(default=None, ge=1950, le=2100)
    accepts_projects: bool = True


@router.put("/designers/me", response_model=DesignerOut, summary="Мой профиль дизайнера")
async def put_me(body: ProfileIn, current: Auth, request: Request, db: DbSession) -> DesignerOut:
    if any(s not in STYLE_KEYS for s in body.styles):
        raise Problem(422, "unknown-style", "Неизвестный стиль")
    p = await _profile(db, current.id)
    created = p is None
    if p is None:
        p = DesignerProfile(id=uuid7(), user_id=current.id, display_name=body.display_name)
        db.add(p)
    for k, v in body.model_dump().items():
        setattr(p, k, v.strip() if isinstance(v, str) else v)
    audit(
        db,
        entity_type="designer_profile",
        entity_id=p.id,
        action="designer.created" if created else "designer.updated",
        actor_id=current.id,
        ip=client_ip(request),
        new={"display_name": p.display_name},
    )
    await db.commit()
    return await _designer_out(db, p, current, full=True)


class VerifyIn(BaseModel):
    verified: bool


@router.put("/designers/{user_id}/verify", response_model=DesignerOut, summary="Проверить")
async def verify(
    user_id: uuid.UUID, body: VerifyIn, current: Auth, request: Request, db: DbSession
) -> DesignerOut:
    if not current.is_platform_admin:
        raise Problem(403, "forbidden", "Дизайнеров проверяет администратор")
    p = await _profile(db, user_id)
    if p is None:
        raise Problem(404, "not-found", "Дизайнер не найден")
    was = p.verified_at is not None
    p.verified_at = (p.verified_at or utcnow()) if body.verified else None
    audit(
        db,
        entity_type="designer_profile",
        entity_id=p.id,
        action="designer.verified",
        actor_id=current.id,
        ip=client_ip(request),
        old={"verified": was},
        new={"verified": body.verified},
    )
    await db.commit()
    return await _designer_out(db, p, current, full=True)


@router.post(
    "/designers/me/photos", response_model=DesignerOut, status_code=201, summary="Фото работ"
)
async def add_photo(
    current: Auth,
    request: Request,
    db: DbSession,
    photo: UploadFile = File(...),
    caption: str | None = Form(default=None, max_length=200),
) -> DesignerOut:
    p = await _profile(db, current.id)
    if p is None:
        raise Problem(409, "no-profile", "Сначала заполните профиль дизайнера")
    count = await db.scalar(
        select(func.count()).select_from(DesignerPhoto).where(DesignerPhoto.profile_id == p.id)
    )
    if (count or 0) >= MAX_PHOTOS:
        raise Problem(409, "portfolio-full", f"Не больше {MAX_PHOTOS} фото")
    s = get_settings()
    data = await photo.read(s.max_photo_bytes + 1)
    if len(data) > s.max_photo_bytes:
        raise Problem(413, "file-too-large", "Фото слишком большое")
    try:
        info = await asyncio.to_thread(inspect_image, data)
    except NotAnImage as exc:
        raise Problem(422, "not-an-image", str(exc)) from None
    pid = uuid7()
    key = f"designers/{p.id}/{pid}.{info.ext}"
    await get_storage().put(key, data, info.content_type)
    db.add(
        DesignerPhoto(
            id=pid,
            profile_id=p.id,
            storage_key=key,
            content_type=info.content_type,
            caption=caption.strip() if caption else None,
        )
    )
    audit(
        db,
        entity_type="designer_profile",
        entity_id=p.id,
        action="designer.photo_added",
        actor_id=current.id,
        ip=client_ip(request),
    )
    await db.commit()
    return await _designer_out(db, p, current, full=True)


@router.delete("/designers/me/photos/{photo_id}", response_model=DesignerOut, summary="Убрать")
async def delete_photo(photo_id: uuid.UUID, current: Auth, db: DbSession) -> DesignerOut:
    p = await _profile(db, current.id)
    photo = await db.get(DesignerPhoto, photo_id)
    if p is None or photo is None or photo.profile_id != p.id:
        raise Problem(404, "not-found", "Фото не найдено")
    await db.delete(photo)
    await db.commit()
    await get_storage().delete(photo.storage_key)
    return await _designer_out(db, p, current, full=True)


# ---------- «Ремонт с душой» на объекте ----------


class ContractOut(BaseModel):
    id: uuid.UUID
    object_id: uuid.UUID
    area_hint: str | None = None  # дизайнеру до согласия — без точного адреса
    area_m2: float | None = None
    style: str | None = None
    designer: DesignerOut | None = None
    status: str
    message: str | None
    fee_kop: int | None
    days: int | None
    concept: str | None
    room_notes: dict[str, str]
    feedback: str | None
    rounds: int
    submitted_at: datetime | None
    approved_at: datetime | None
    created_at: datetime


class SoulOut(BaseModel):
    soul: bool
    can_change: bool
    is_designer: bool
    finalized_at: datetime | None
    contract: ContractOut | None
    history: list[ContractOut]


def _area_hint(address: str) -> str:
    """Город и улица без дома и квартиры."""
    parts = [x.strip() for x in address.split(",")]
    return ", ".join(parts[:2]) if len(parts) > 1 else parts[0][:40]


async def _contract_out(
    db: DbSession, c: DesignerContract, current: CurrentUser, *, show_phone: bool
) -> ContractOut:
    obj = await db.get(RepairObject, c.object_id)
    prof = await _profile(db, c.designer_id)
    user = await db.get(User, c.designer_id)
    project = (
        await db.execute(select(DesignProject).where(DesignProject.object_id == c.object_id))
    ).scalar_one_or_none()
    return ContractOut(
        id=c.id,
        object_id=c.object_id,
        area_hint=_area_hint(obj.address_text) if obj else None,
        area_m2=float(obj.area_m2) if obj and obj.area_m2 is not None else None,
        style=project.style if project else None,
        designer=await _designer_out(
            db,
            prof,
            current,
            phone=user.phone
            if user and show_phone and c.status in ("working", "submitted", "approved")
            else None,
        )
        if prof
        else None,
        status=c.status,
        message=c.message,
        fee_kop=c.fee_kop,
        days=c.days,
        concept=c.concept,
        room_notes=c.room_notes,
        feedback=c.feedback,
        rounds=c.rounds,
        submitted_at=c.submitted_at,
        approved_at=c.approved_at,
        created_at=c.created_at,
    )


async def _contracts(db: DbSession, object_id: uuid.UUID) -> list[DesignerContract]:
    return list(
        (
            await db.execute(
                select(DesignerContract)
                .where(DesignerContract.object_id == object_id)
                .order_by(DesignerContract.created_at.desc())
            )
        ).scalars()
    )


async def _project(db: DbSession, object_id: uuid.UUID) -> DesignProject | None:
    return (
        await db.execute(select(DesignProject).where(DesignProject.object_id == object_id))
    ).scalar_one_or_none()


async def _soul_out(db: DbSession, access: ObjectAccess, current: CurrentUser) -> SoulOut:
    cs = await _contracts(db, access.obj.id)
    active = next((c for c in cs if c.status in ACTIVE), None)
    approved = next((c for c in cs if c.status == "approved"), None)
    current_c = active or approved
    p = await _project(db, access.obj.id)
    return SoulOut(
        soul=access.obj.soul,
        can_change=access.is_customer or access.is_admin,
        is_designer=access.is_designer,
        finalized_at=p.finalized_at if p else None,
        contract=await _contract_out(db, current_c, current, show_phone=True)
        if current_c
        else None,
        history=[
            await _contract_out(db, c, current, show_phone=False) for c in cs if c is not current_c
        ],
    )


@router.get("/objects/{object_id}/soul", response_model=SoulOut, summary="Ремонт с душой")
async def get_soul(object_id: uuid.UUID, current: Auth, db: DbSession) -> SoulOut:
    access = await load_object(db, current, object_id)
    return await _soul_out(db, access, current)


class SoulIn(BaseModel):
    soul: bool


@router.put("/objects/{object_id}/soul", response_model=SoulOut, summary="Включить или выключить")
async def set_soul(
    object_id: uuid.UUID, body: SoulIn, current: Auth, request: Request, db: DbSession
) -> SoulOut:
    access = await load_object(db, current, object_id, lock=True)
    if not (access.is_customer or access.is_admin):
        raise Problem(403, "forbidden", "Это выбирает заказчик")
    obj = access.obj
    if not body.soul and any(c.status in ACTIVE for c in await _contracts(db, obj.id)):
        raise Problem(409, "designer-active", "Сначала завершите работу с дизайнером")
    if obj.soul != body.soul:
        audit(
            db,
            entity_type="object",
            entity_id=obj.id,
            action="object.soul_set",
            actor_id=current.id,
            ip=client_ip(request),
            old={"soul": obj.soul},
            new={"soul": body.soul},
        )
        obj.soul = body.soul
        await db.commit()
    return await _soul_out(db, access, current)


class InviteIn(BaseModel):
    designer_id: uuid.UUID
    message: str | None = Field(default=None, max_length=2000)


@router.post(
    "/objects/{object_id}/designer", response_model=SoulOut, status_code=201, summary="Пригласить"
)
async def invite(
    object_id: uuid.UUID, body: InviteIn, current: Auth, request: Request, db: DbSession
) -> SoulOut:
    access = await load_object(db, current, object_id, lock=True)
    if not (access.is_customer or access.is_admin):
        raise Problem(403, "forbidden", "Дизайнера выбирает заказчик")
    obj = access.obj
    if not obj.soul:
        raise Problem(409, "not-soul", "Сначала включите «Ремонт с душой»")
    if await _project(db, obj.id) is None:
        raise Problem(409, "no-design", "Сначала выберите стиль в дизайн-проекте")
    if any(c.status in ACTIVE for c in await _contracts(db, obj.id)):
        raise Problem(409, "designer-active", "Дизайнер уже приглашён")
    prof = await _profile(db, body.designer_id)
    if prof is None or prof.verified_at is None or not prof.accepts_projects:
        raise Problem(404, "not-found", "Дизайнер не найден или не берёт проекты")
    if body.designer_id == obj.customer_id:
        raise Problem(422, "self-invite", "Нельзя пригласить себя")
    c = DesignerContract(
        id=uuid7(),
        object_id=obj.id,
        designer_id=body.designer_id,
        status="invited",
        message=(body.message or "").strip() or None,
        room_notes={},
    )
    db.add(c)
    audit(
        db,
        entity_type="designer_contract",
        entity_id=c.id,
        action="designer.invited",
        actor_id=current.id,
        ip=client_ip(request),
        new={"designer_id": body.designer_id},
    )
    notes.notify(
        db,
        [body.designer_id],
        "designer_invited",
        area=_area_hint(obj.address_text),
        url=notes.url("/designer"),
    )
    await db.commit()
    return await _soul_out(db, access, current)


@router.get(
    "/designers/me/projects", response_model=list[ContractOut], summary="Мои проекты дизайнера"
)
async def my_projects(current: Auth, db: DbSession) -> list[ContractOut]:
    cs = (
        await db.execute(
            select(DesignerContract)
            .where(DesignerContract.designer_id == current.id)
            .order_by(DesignerContract.updated_at.desc())
            .limit(100)
        )
    ).scalars()
    return [await _contract_out(db, c, current, show_phone=False) for c in cs]


async def _contract(
    db: DbSession, current: CurrentUser, contract_id: uuid.UUID
) -> tuple[DesignerContract, RepairObject]:
    c = await db.get(DesignerContract, contract_id, with_for_update=True)
    if c is None:
        raise Problem(404, "not-found", "Приглашение не найдено")
    obj = await db.get(RepairObject, c.object_id)
    assert obj is not None
    return c, obj


def _need(c: DesignerContract, *statuses: str) -> None:
    if c.status not in statuses:
        raise Problem(409, "bad-transition", "Этот шаг сейчас недоступен")


class OfferIn(BaseModel):
    fee_kop: int = Field(gt=0, le=10**11)
    days: int = Field(ge=1, le=365)
    message: str | None = Field(default=None, max_length=2000)


@router.post(
    "/designer-contracts/{contract_id}/offer", response_model=ContractOut, summary="Мои условия"
)
async def offer(
    contract_id: uuid.UUID, body: OfferIn, current: Auth, request: Request, db: DbSession
) -> ContractOut:
    c, obj = await _contract(db, current, contract_id)
    if c.designer_id != current.id:
        raise Problem(404, "not-found", "Приглашение не найдено")
    _need(c, "invited", "offered")
    c.fee_kop, c.days, c.status = body.fee_kop, body.days, "offered"
    if body.message:
        c.message = body.message.strip()
    prof = await _profile(db, current.id)
    audit(
        db,
        entity_type="designer_contract",
        entity_id=c.id,
        action="designer.offered",
        actor_id=current.id,
        ip=client_ip(request),
        new={"fee_kop": body.fee_kop, "days": body.days},
    )
    notes.notify(
        db,
        [obj.customer_id],
        "designer_offer",
        name=prof.display_name if prof else "",
        fee=notes.rub(body.fee_kop),
        days=body.days,
        url=notes.url(f"/objects/{obj.id}/design#designer"),
    )
    await db.commit()
    return await _contract_out(db, c, current, show_phone=False)


@router.post(
    "/designer-contracts/{contract_id}/decline", response_model=ContractOut, summary="Отказаться"
)
async def decline(
    contract_id: uuid.UUID, current: Auth, request: Request, db: DbSession
) -> ContractOut:
    c, obj = await _contract(db, current, contract_id)
    if c.designer_id != current.id:
        raise Problem(404, "not-found", "Приглашение не найдено")
    _need(c, "invited", "offered")
    c.status = "declined"
    prof = await _profile(db, current.id)
    audit(
        db,
        entity_type="designer_contract",
        entity_id=c.id,
        action="designer.declined",
        actor_id=current.id,
        ip=client_ip(request),
    )
    notes.notify(
        db,
        [obj.customer_id],
        "designer_declined",
        name=prof.display_name if prof else "",
        url=notes.url(f"/objects/{obj.id}/design#designer"),
    )
    await db.commit()
    return await _contract_out(db, c, current, show_phone=False)


async def _customer_side(db: DbSession, current: CurrentUser, c: DesignerContract) -> ObjectAccess:
    access = await load_object(db, current, c.object_id, lock=True)
    if not (access.is_customer or access.is_admin):
        raise Problem(403, "forbidden", "Это решает заказчик")
    return access


@router.post(
    "/designer-contracts/{contract_id}/accept", response_model=SoulOut, summary="Принять условия"
)
async def accept(contract_id: uuid.UUID, current: Auth, request: Request, db: DbSession) -> SoulOut:
    c, obj = await _contract(db, current, contract_id)
    access = await _customer_side(db, current, c)
    _need(c, "offered")
    c.status = "working"
    await ensure_assignment(db, obj.id, c.designer_id, AssignmentRole.designer)
    audit(
        db,
        entity_type="designer_contract",
        entity_id=c.id,
        action="designer.accepted",
        actor_id=current.id,
        ip=client_ip(request),
        new={"fee_kop": c.fee_kop},
    )
    notes.notify(
        db, [c.designer_id], "designer_started", url=notes.url(f"/objects/{obj.id}/design")
    )
    await db.commit()
    access = await load_object(db, current, obj.id)
    return await _soul_out(db, access, current)


async def _unassign(db: DbSession, object_id: uuid.UUID, user_id: uuid.UUID) -> None:
    row = (
        await db.execute(
            select(ObjectAssignment).where(
                ObjectAssignment.object_id == object_id,
                ObjectAssignment.user_id == user_id,
                ObjectAssignment.role == AssignmentRole.designer,
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        await db.delete(row)


@router.post("/designer-contracts/{contract_id}/cancel", response_model=SoulOut, summary="Отменить")
async def cancel(contract_id: uuid.UUID, current: Auth, request: Request, db: DbSession) -> SoulOut:
    c, obj = await _contract(db, current, contract_id)
    access = await _customer_side(db, current, c)
    _need(c, "invited", "offered", "working", "submitted")
    old = c.status
    c.status = "cancelled"
    await _unassign(db, obj.id, c.designer_id)
    audit(
        db,
        entity_type="designer_contract",
        entity_id=c.id,
        action="designer.cancelled",
        actor_id=current.id,
        ip=client_ip(request),
        old={"status": old},
    )
    await db.commit()
    return await _soul_out(db, access, current)


class ConceptIn(BaseModel):
    concept: str | None = Field(default=None, max_length=10000)
    room_notes: dict[str, str] = Field(default_factory=dict)


@router.put(
    "/designer-contracts/{contract_id}/concept", response_model=ContractOut, summary="Концепция"
)
async def put_concept(
    contract_id: uuid.UUID, body: ConceptIn, current: Auth, request: Request, db: DbSession
) -> ContractOut:
    c, _ = await _contract(db, current, contract_id)
    if c.designer_id != current.id:
        raise Problem(403, "forbidden", "Концепцию пишет дизайнер")
    _need(c, "working")
    notes_ = {k[:20]: v.strip()[:3000] for k, v in body.room_notes.items() if v.strip()}
    if len(notes_) > 30:
        raise Problem(422, "too-many", "Слишком много комнат")
    c.concept = (body.concept or "").strip() or None
    c.room_notes = notes_
    audit(
        db,
        entity_type="designer_contract",
        entity_id=c.id,
        action="designer.concept",
        actor_id=current.id,
        ip=client_ip(request),
    )
    await db.commit()
    return await _contract_out(db, c, current, show_phone=False)


@router.post(
    "/designer-contracts/{contract_id}/submit", response_model=ContractOut, summary="Сдать проект"
)
async def submit(
    contract_id: uuid.UUID, current: Auth, request: Request, db: DbSession
) -> ContractOut:
    c, obj = await _contract(db, current, contract_id)
    if c.designer_id != current.id:
        raise Problem(403, "forbidden", "Проект сдаёт дизайнер")
    _need(c, "working")
    if not c.concept:
        raise Problem(422, "no-concept", "Опишите концепцию перед сдачей")
    c.status, c.submitted_at = "submitted", utcnow()
    c.rounds += 1
    audit(
        db,
        entity_type="designer_contract",
        entity_id=c.id,
        action="designer.submitted",
        actor_id=current.id,
        ip=client_ip(request),
        new={"round": c.rounds},
    )
    notes.notify(
        db, [obj.customer_id], "design_submitted", url=notes.url(f"/objects/{obj.id}/design")
    )
    await db.commit()
    return await _contract_out(db, c, current, show_phone=False)


class ReviewIn(BaseModel):
    approve: bool
    feedback: str | None = Field(default=None, max_length=5000)


@router.post(
    "/designer-contracts/{contract_id}/review", response_model=SoulOut, summary="Утвердить"
)
async def review(
    contract_id: uuid.UUID, body: ReviewIn, current: Auth, request: Request, db: DbSession
) -> SoulOut:
    c, obj = await _contract(db, current, contract_id)
    access = await _customer_side(db, current, c)
    p = await _project(db, obj.id)
    if body.approve:
        _need(c, "submitted")
        now = utcnow()
        c.status, c.approved_at = "approved", now
        if p is not None:
            p.finalized_at, p.finalized_by = now, c.designer_id
        template, action = "design_approved", "designer.approved"
    else:
        # Доработка: из сданного проекта или после утверждения (тогда дизайн снова открыт).
        _need(c, "submitted", "approved")
        if not (body.feedback or "").strip():
            raise Problem(422, "no-feedback", "Напишите, что доработать")
        c.status, c.feedback = "working", body.feedback.strip() if body.feedback else None
        c.approved_at = None
        if p is not None:
            p.finalized_at = p.finalized_by = None
        await ensure_assignment(db, obj.id, c.designer_id, AssignmentRole.designer)
        template, action = "design_feedback", "designer.feedback"
    audit(
        db,
        entity_type="designer_contract",
        entity_id=c.id,
        action=action,
        actor_id=current.id,
        ip=client_ip(request),
    )
    notes.notify(db, [c.designer_id], template, url=notes.url(f"/objects/{obj.id}/design"))
    await db.commit()
    access = await load_object(db, current, obj.id)
    return await _soul_out(db, access, current)


# ---------- наполнение предметами ----------


class DecorOut(BaseModel):
    id: uuid.UUID
    room_key: str
    title: str
    note: str | None
    link: str | None
    furniture_item_id: uuid.UUID | None
    price_kop: int | None
    qty: int
    by_designer: bool


class DecorIn(BaseModel):
    room_key: str = Field(min_length=1, max_length=20)
    title: str | None = Field(default=None, min_length=2, max_length=300)
    note: str | None = Field(default=None, max_length=2000)
    link: str | None = Field(default=None, max_length=500, pattern=r"^https?://")
    furniture_item_id: uuid.UUID | None = None
    price_kop: int | None = Field(default=None, gt=0, le=10**11)
    qty: int = Field(default=1, ge=1, le=100)


async def _decor_list(db: DbSession, p: DesignProject, obj: RepairObject) -> list[DecorOut]:
    designers = {
        u
        for u in (
            await db.execute(
                select(DesignerContract.designer_id).where(DesignerContract.object_id == obj.id)
            )
        ).scalars()
    }
    rows = (
        await db.execute(
            select(DesignDecor)
            .where(DesignDecor.project_id == p.id)
            .order_by(DesignDecor.room_key, DesignDecor.created_at)
        )
    ).scalars()
    return [
        DecorOut(
            id=d.id,
            room_key=d.room_key,
            title=d.title,
            note=d.note,
            link=d.link,
            furniture_item_id=d.furniture_item_id,
            price_kop=d.price_kop,
            qty=d.qty,
            by_designer=d.added_by in designers,
        )
        for d in rows
    ]


async def _decor_project(
    db: DbSession, current: CurrentUser, object_id: uuid.UUID, *, edit: bool
) -> tuple[DesignProject, ObjectAccess]:
    access = await load_object(db, current, object_id, lock=edit)
    p = await _project(db, object_id)
    if p is None:
        raise Problem(404, "not-found", "Дизайн-проекта нет")
    if edit:
        if not (access.is_customer or access.is_admin or access.is_designer):
            raise Problem(403, "forbidden", "Наполнение ведут заказчик и дизайнер")
        if access.obj.soul and p.finalized_at is not None:
            raise Problem(409, "design-final", "Дизайн утверждён")
    return p, access


@router.get(
    "/objects/{object_id}/design/decor", response_model=list[DecorOut], summary="Наполнение"
)
async def decor(object_id: uuid.UUID, current: Auth, db: DbSession) -> list[DecorOut]:
    p, access = await _decor_project(db, current, object_id, edit=False)
    return await _decor_list(db, p, access.obj)


@router.post(
    "/objects/{object_id}/design/decor",
    response_model=list[DecorOut],
    status_code=201,
    summary="Добавить предмет",
)
async def add_decor(
    object_id: uuid.UUID, body: DecorIn, current: Auth, request: Request, db: DbSession
) -> list[DecorOut]:
    p, access = await _decor_project(db, current, object_id, edit=True)
    if body.room_key not in {r["key"] for r in p.rooms}:
        raise Problem(404, "not-found", "Комнаты нет в проекте")
    title, price = body.title, body.price_kop
    if body.furniture_item_id is not None:
        item = await db.get(FurnitureItem, body.furniture_item_id)
        if item is None or not item.is_active:
            raise Problem(404, "not-found", "Позиции нет в каталоге")
        title, price = title or item.title, price or item.price_rub_kop
    if not title:
        raise Problem(422, "no-title", "Назовите предмет")
    d = DesignDecor(
        id=uuid7(),
        project_id=p.id,
        room_key=body.room_key,
        title=title.strip(),
        note=(body.note or "").strip() or None,
        link=body.link,
        furniture_item_id=body.furniture_item_id,
        price_kop=price,
        qty=body.qty,
        added_by=current.id,
    )
    db.add(d)
    audit(
        db,
        entity_type="design_project",
        entity_id=p.id,
        action="design.decor_added",
        actor_id=current.id,
        ip=client_ip(request),
        new={"room": body.room_key, "title": d.title},
    )
    await db.commit()
    return await _decor_list(db, p, access.obj)


@router.delete(
    "/objects/{object_id}/design/decor/{decor_id}",
    response_model=list[DecorOut],
    summary="Убрать предмет",
)
async def delete_decor(
    object_id: uuid.UUID, decor_id: uuid.UUID, current: Auth, db: DbSession
) -> list[DecorOut]:
    p, access = await _decor_project(db, current, object_id, edit=True)
    d = await db.get(DesignDecor, decor_id)
    if d is None or d.project_id != p.id:
        raise Problem(404, "not-found", "Предмет не найден")
    await db.delete(d)
    await db.commit()
    return await _decor_list(db, p, access.obj)
