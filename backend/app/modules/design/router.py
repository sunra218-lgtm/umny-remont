"""Умный дизайн-проект: стиль → план БТИ и комнаты → отделка из каталога → оценка стоимости →
картинки комнат и ролик.

Заказчик (а также администратор и сметчик платформы) создаёт и правит проект; бригада объекта
видит его. Каталог отделки ведут администратор и менеджер закупок.
"""

import asyncio
import uuid
from datetime import datetime

from fastapi import APIRouter, File, Form, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.access import ObjectAccess, load_object
from app.core.audit import audit
from app.core.deps import Auth, CurrentUser, DbSession, client_ip
from app.core.ids import uuid7
from app.core.problems import Problem
from app.core.security import utcnow
from app.core.storage import get_storage, signed_file_url
from app.domain.design import ROOM_SLOTS, ROOM_TYPES, SLOTS, STYLE_KEYS, STYLES
from app.models import DesignMaterial, DesignProject, DesignRender
from app.models.enums import OrgRole
from app.modules.design import integrations as ext
from app.modules.design import service
from app.modules.design.service import Estimate, Room
from app.modules.field.antifraud import NotAnImage, inspect_image

router = APIRouter(tags=["design"])

MAX_PLAN_BYTES = 10 * 1024 * 1024


def _can_edit(access: ObjectAccess) -> bool:
    return (
        access.is_customer or access.is_admin or access.is_platform_estimator or access.is_designer
    )


def _check_open(p: DesignProject, access: ObjectAccess) -> None:
    """В «Ремонте с душой» утверждённый дизайн меняется только через доработку у дизайнера."""
    if access.obj.soul and p.finalized_at is not None:
        raise Problem(
            409,
            "design-final",
            "Дизайн утверждён",
            "Чтобы что-то поменять, попросите дизайнера доработать проект.",
        )


# ---------- справочники ----------


class StyleOut(BaseModel):
    key: str
    title: str
    about: str
    palette: list[str]


class MaterialOut(BaseModel):
    id: uuid.UUID
    sku: str
    kind: str
    title: str
    brand: str | None
    styles: list[str]
    color: str
    pattern: str
    unit: str
    price_kop: int
    rate: float
    pack_size: float
    pack_title: str | None
    is_active: bool


class DesignInfo(BaseModel):
    styles: list[StyleOut]
    room_types: dict[str, str]
    slots: dict[str, str]
    room_slots: dict[str, dict[str, list[str]]]
    plan_reader: bool
    renderer: bool
    video: bool


@router.get("/design/info", response_model=DesignInfo, summary="Стили, комнаты и что подключено")
async def info(current: Auth) -> DesignInfo:
    return DesignInfo(
        styles=[StyleOut(**s) for s in STYLES],
        room_types=ROOM_TYPES,
        slots=SLOTS,
        room_slots=ROOM_SLOTS,
        plan_reader=ext.plan_reader_enabled(),
        renderer=ext.renderer_enabled(),
        video=ext.video_enabled(),
    )


def _mat_out(m: DesignMaterial) -> MaterialOut:
    return MaterialOut.model_validate(m, from_attributes=True)


@router.get("/design/materials", response_model=list[MaterialOut], summary="Каталог отделки")
async def materials(
    current: Auth, db: DbSession, kind: str | None = None, all: bool = False
) -> list[MaterialOut]:
    stmt = select(DesignMaterial).order_by(DesignMaterial.kind, DesignMaterial.price_kop)
    if kind:
        stmt = stmt.where(DesignMaterial.kind == kind)
    if not (all and _catalog_editor(current)):
        stmt = stmt.where(DesignMaterial.is_active.is_(True))
    return [_mat_out(m) for m in (await db.execute(stmt)).scalars()]


def _catalog_editor(current: CurrentUser) -> bool:
    return current.has_platform_role(OrgRole.platform_admin, OrgRole.procurement_manager)


class MaterialIn(BaseModel):
    sku: str = Field(min_length=2, max_length=50)
    kind: str
    title: str = Field(min_length=2, max_length=300)
    brand: str | None = Field(default=None, max_length=100)
    styles: list[str] = Field(default_factory=list)
    color: str = Field(default="#CCCCCC", pattern=r"^#[0-9A-Fa-f]{6}$")
    pattern: str = Field(default="none", max_length=20)
    unit: str = Field(min_length=1, max_length=10)
    price_kop: int = Field(gt=0, le=10**10)
    rate: float = Field(default=1.0, gt=0, le=100)
    pack_size: float = Field(default=1.0, gt=0, le=1000)
    pack_title: str | None = Field(default=None, max_length=50)
    is_active: bool = True


def _check_material(body: MaterialIn) -> None:
    from app.domain.design import KIND_RULES

    if body.kind not in KIND_RULES:
        raise Problem(422, "unknown-kind", "Неизвестный вид материала")
    bad = [s for s in body.styles if s not in STYLE_KEYS]
    if bad:
        raise Problem(422, "unknown-style", f"Неизвестный стиль: {bad[0]}")


@router.post(
    "/design/materials", response_model=MaterialOut, status_code=201, summary="Добавить отделку"
)
async def add_material(
    body: MaterialIn, current: Auth, request: Request, db: DbSession
) -> MaterialOut:
    if not _catalog_editor(current):
        raise Problem(403, "forbidden", "Каталог ведут администратор и менеджер закупок")
    _check_material(body)
    if await db.scalar(select(DesignMaterial.id).where(DesignMaterial.sku == body.sku)):
        raise Problem(409, "sku-exists", "Такой артикул уже есть")
    m = DesignMaterial(id=uuid7(), **body.model_dump())
    db.add(m)
    audit(
        db,
        entity_type="design_material",
        entity_id=m.id,
        action="design_material.created",
        actor_id=current.id,
        ip=client_ip(request),
        new={"sku": m.sku, "price_kop": m.price_kop},
    )
    await db.commit()
    return _mat_out(m)


@router.put("/design/materials/{material_id}", response_model=MaterialOut, summary="Изменить")
async def put_material(
    material_id: uuid.UUID, body: MaterialIn, current: Auth, request: Request, db: DbSession
) -> MaterialOut:
    if not _catalog_editor(current):
        raise Problem(403, "forbidden", "Каталог ведут администратор и менеджер закупок")
    _check_material(body)
    m = await db.get(DesignMaterial, material_id)
    if m is None:
        raise Problem(404, "not-found", "Материал не найден")
    old = {"price_kop": m.price_kop, "is_active": m.is_active}
    for k, v in body.model_dump().items():
        setattr(m, k, v)
    audit(
        db,
        entity_type="design_material",
        entity_id=m.id,
        action="design_material.updated",
        actor_id=current.id,
        ip=client_ip(request),
        old=old,
        new={"price_kop": m.price_kop, "is_active": m.is_active},
    )
    await db.commit()
    return _mat_out(m)


# ---------- проект ----------


class RenderOut(BaseModel):
    room_key: str
    status: str
    url: str | None
    error: str | None


class ProjectOut(BaseModel):
    id: uuid.UUID
    object_id: uuid.UUID
    style: str
    rooms: list[Room]
    selections: dict[str, dict[str, str]]
    pinned: dict[str, list[str]]
    materials: list[MaterialOut]  # всё, что выбрано, — для картинки и подписи
    plan_url: str | None
    plan_text: str | None
    version: int
    updated_at: datetime
    can_edit: bool
    renders: list[RenderOut]
    video_status: str | None
    video_url: str | None
    soul: bool = False
    finalized_at: datetime | None = None


async def _project(db: DbSession, object_id: uuid.UUID) -> DesignProject | None:
    return (
        await db.execute(select(DesignProject).where(DesignProject.object_id == object_id))
    ).scalar_one_or_none()


async def _out(
    db: DbSession, p: DesignProject, access: ObjectAccess, current: CurrentUser
) -> ProjectOut:
    await db.refresh(p)  # updated_at ставит база при сохранении
    ids = {mid for sel in p.selections.values() for mid in sel.values()}
    mats = (
        [
            _mat_out(m)
            for m in (
                await db.execute(
                    select(DesignMaterial).where(DesignMaterial.id.in_([uuid.UUID(i) for i in ids]))
                )
            ).scalars()
        ]
        if ids
        else []
    )
    renders = (
        await db.execute(
            select(DesignRender)
            .where(DesignRender.project_id == p.id, DesignRender.version == p.version)
            .order_by(DesignRender.created_at)
        )
    ).scalars()
    return ProjectOut(
        id=p.id,
        object_id=p.object_id,
        style=p.style,
        rooms=[Room(**r) for r in p.rooms],
        selections=p.selections,
        pinned=p.pinned,
        materials=mats,
        plan_url=signed_file_url("plan", p.id, current.id) if p.plan_key else None,
        plan_text=p.plan_text,
        version=p.version,
        updated_at=p.updated_at,
        can_edit=_can_edit(access),
        renders=[
            RenderOut(
                room_key=r.room_key,
                status=r.status,
                url=signed_file_url("render", r.id, current.id) if r.storage_key else None,
                error=r.error,
            )
            for r in renders
        ],
        video_status=p.video_status,
        video_url=p.video_url,
        soul=access.obj.soul,
        finalized_at=p.finalized_at,
    )


async def _editable(
    db: DbSession, current: CurrentUser, object_id: uuid.UUID
) -> tuple[DesignProject, ObjectAccess]:
    access = await load_object(db, current, object_id, lock=True)
    if not _can_edit(access):
        raise Problem(403, "forbidden", "Дизайн-проект ведут заказчик и дизайнер")
    p = await _project(db, object_id)
    if p is None:
        raise Problem(404, "not-found", "Сначала выберите стиль")
    _check_open(p, access)
    return p, access


def _bump(p: DesignProject) -> None:
    """Любая правка отделки делает прежние картинки и ролик устаревшими."""
    p.version += 1
    p.video_status, p.video_url, p.video_job_id = None, None, None


@router.get(
    "/objects/{object_id}/design", response_model=ProjectOut | None, summary="Дизайн-проект"
)
async def get_project(object_id: uuid.UUID, current: Auth, db: DbSession) -> ProjectOut | None:
    access = await load_object(db, current, object_id)
    p = await _project(db, object_id)
    return await _out(db, p, access, current) if p else None


class StyleIn(BaseModel):
    style: str
    rooms_count: int = Field(default=2, ge=1, le=6)


@router.put("/objects/{object_id}/design", response_model=ProjectOut, summary="Выбрать стиль")
async def put_style(
    object_id: uuid.UUID, body: StyleIn, current: Auth, request: Request, db: DbSession
) -> ProjectOut:
    if body.style not in STYLE_KEYS:
        raise Problem(422, "unknown-style", "Неизвестный стиль")
    access = await load_object(db, current, object_id, lock=True)
    if not _can_edit(access):
        raise Problem(403, "forbidden", "Дизайн-проект ведут заказчик и дизайнер")
    p = await _project(db, object_id)
    if p is not None:
        _check_open(p, access)
    created = p is None
    if p is None:
        rooms = service.suggest_rooms(
            float(access.obj.area_m2) if access.obj.area_m2 else None, body.rooms_count
        )
        p = DesignProject(
            id=uuid7(),
            object_id=object_id,
            style=body.style,
            rooms=[r.model_dump() for r in rooms],
            selections={},
            pinned={},
            created_by=current.id,
        )
        db.add(p)
    old_style = p.style
    p.style = body.style
    rooms = [Room(**r) for r in p.rooms]
    p.selections = service.auto_select(p, rooms, await service.active_materials(db))
    _bump(p)
    await db.flush()
    audit(
        db,
        entity_type="design_project",
        entity_id=p.id,
        action="design.created" if created else "design.style_changed",
        actor_id=current.id,
        ip=client_ip(request),
        old=None if created else {"style": old_style},
        new={"style": body.style},
    )
    await db.commit()
    return await _out(db, p, access, current)


@router.post(
    "/objects/{object_id}/design/plan", response_model=ProjectOut, summary="Загрузить план БТИ"
)
async def upload_plan(
    object_id: uuid.UUID,
    current: Auth,
    request: Request,
    db: DbSession,
    plan: UploadFile = File(...),
    replace_rooms: bool = Form(default=True),
) -> ProjectOut:
    p, access = await _editable(db, current, object_id)
    data = await plan.read(MAX_PLAN_BYTES + 1)
    if len(data) > MAX_PLAN_BYTES:
        raise Problem(413, "file-too-large", "План больше 10 МБ")
    if data[:5] == b"%PDF-":
        content_type, ext_ = "application/pdf", "pdf"
    else:
        try:
            info = await asyncio.to_thread(inspect_image, data)
        except NotAnImage:
            # План часто сканируют в PNG — inspect_image принимает только фото.
            if data[:8] == b"\x89PNG\r\n\x1a\n":
                content_type, ext_ = "image/png", "png"
            else:
                raise Problem(422, "bad-plan", "Нужен план в PDF, JPEG или PNG") from None
        else:
            content_type, ext_ = info.content_type, info.ext
    key = f"plans/{object_id}/{uuid7()}.{ext_}"
    await get_storage().put(key, data, content_type)
    p.plan_key, p.plan_type = key, content_type
    found: list[Room] = []
    if ext.plan_reader_enabled():
        try:
            p.plan_text = (await ext.read_plan(data, content_type))[:20000]
            found = service.parse_explication(p.plan_text)
        except Exception as exc:  # noqa: BLE001 — сбой распознавания не мешает загрузке плана
            p.plan_text = None
            import logging

            logging.getLogger(__name__).warning("План не распознан: %s", exc)
    if found and replace_rooms:
        p.rooms = [r.model_dump() for r in found]
        p.pinned = {}
        p.selections = service.auto_select(p, found, await service.active_materials(db))
        _bump(p)
    audit(
        db,
        entity_type="design_project",
        entity_id=p.id,
        action="design.plan_uploaded",
        actor_id=current.id,
        ip=client_ip(request),
        new={"rooms_found": len(found)},
    )
    await db.commit()
    return await _out(db, p, access, current)


class RoomsIn(BaseModel):
    rooms: list[Room] = Field(min_length=1, max_length=20)


@router.put("/objects/{object_id}/design/rooms", response_model=ProjectOut, summary="Комнаты")
async def put_rooms(
    object_id: uuid.UUID, body: RoomsIn, current: Auth, request: Request, db: DbSession
) -> ProjectOut:
    p, access = await _editable(db, current, object_id)
    keys = [r.key for r in body.rooms]
    if len(set(keys)) != len(keys):
        raise Problem(422, "duplicate-room", "У комнат повторяются ключи")
    # Комната сменила тип — её выбор отделки уже не подходит.
    old_types = {r["key"]: r["type"] for r in p.rooms}
    pinned = {
        k: v
        for k, v in p.pinned.items()
        if k in keys and old_types.get(k) == next(r.type for r in body.rooms if r.key == k)
    }
    selections = {k: v for k, v in p.selections.items() if k in pinned}
    p.rooms = [r.model_dump() for r in body.rooms]
    p.pinned = pinned
    p.selections = selections
    p.selections = service.auto_select(p, body.rooms, await service.active_materials(db))
    _bump(p)
    audit(
        db,
        entity_type="design_project",
        entity_id=p.id,
        action="design.rooms_updated",
        actor_id=current.id,
        ip=client_ip(request),
        new={"rooms": len(body.rooms)},
    )
    await db.commit()
    return await _out(db, p, access, current)


class ChoiceIn(BaseModel):
    material_id: uuid.UUID | None = None  # None — вернуть подбор по стилю


@router.put(
    "/objects/{object_id}/design/rooms/{room_key}/{slot}",
    response_model=ProjectOut,
    summary="Поменять отделку в комнате",
)
async def choose(
    object_id: uuid.UUID,
    room_key: str,
    slot: str,
    body: ChoiceIn,
    current: Auth,
    request: Request,
    db: DbSession,
) -> ProjectOut:
    p, access = await _editable(db, current, object_id)
    room = next((Room(**r) for r in p.rooms if r["key"] == room_key), None)
    if room is None:
        raise Problem(404, "not-found", "Комнаты нет в проекте")
    if slot not in ROOM_SLOTS[room.type]:
        raise Problem(422, "bad-slot", "В этой комнате такого нет")
    selections = {k: dict(v) for k, v in p.selections.items()}
    pinned = {k: list(v) for k, v in p.pinned.items()}
    if body.material_id is None:
        pinned[room_key] = [s for s in pinned.get(room_key, []) if s != slot]
        m = service.pick(await service.active_materials(db), room.type, slot, p.style)
        if m:
            selections.setdefault(room_key, {})[slot] = str(m.id)
    else:
        m = await db.get(DesignMaterial, body.material_id)
        if m is None or not m.is_active:
            raise Problem(404, "not-found", "Материала нет в каталоге")
        if m.kind not in ROOM_SLOTS[room.type][slot]:
            raise Problem(422, "wrong-kind", "Этот материал сюда не подходит")
        selections.setdefault(room_key, {})[slot] = str(m.id)
        pinned[room_key] = sorted({*pinned.get(room_key, []), slot})
    p.selections, p.pinned = selections, pinned
    _bump(p)
    audit(
        db,
        entity_type="design_project",
        entity_id=p.id,
        action="design.choice",
        actor_id=current.id,
        ip=client_ip(request),
        new={"room": room_key, "slot": slot, "material_id": body.material_id},
    )
    await db.commit()
    return await _out(db, p, access, current)


@router.get(
    "/objects/{object_id}/design/estimate", response_model=Estimate, summary="Оценка стоимости"
)
async def get_estimate(object_id: uuid.UUID, current: Auth, db: DbSession) -> Estimate:
    await load_object(db, current, object_id)
    p = await _project(db, object_id)
    if p is None:
        raise Problem(404, "not-found", "Дизайн-проекта нет")
    return await service.estimate(db, p)


# ---------- картинки и ролик ----------


@router.post(
    "/objects/{object_id}/design/renders",
    response_model=ProjectOut,
    summary="Нарисовать комнаты нейросетью",
)
async def order_renders(
    object_id: uuid.UUID, current: Auth, request: Request, db: DbSession
) -> ProjectOut:
    p, access = await _editable(db, current, object_id)
    if not ext.renderer_enabled():
        raise Problem(409, "renderer-off", "Нейросеть для картинок не подключена")
    existing = {
        r.room_key
        for r in (
            await db.execute(
                select(DesignRender).where(
                    DesignRender.project_id == p.id,
                    DesignRender.version == p.version,
                    DesignRender.status != "failed",
                )
            )
        ).scalars()
    }
    mats = {str(m.id): m for m in await service.active_materials(db)}
    for r in p.rooms:
        room = Room(**r)
        if room.key in existing:
            continue
        brief = service.room_brief(p, room, mats)
        db.add(
            DesignRender(
                id=uuid7(),
                project_id=p.id,
                room_key=room.key,
                version=p.version,
                status="queued",
                prompt=ext.render_prompt(p.style, brief),
            )
        )
    audit(
        db,
        entity_type="design_project",
        entity_id=p.id,
        action="design.renders_ordered",
        actor_id=current.id,
        ip=client_ip(request),
    )
    await db.commit()
    return await _out(db, p, access, current)


@router.post(
    "/objects/{object_id}/design/video", response_model=ProjectOut, summary="Заказать ролик"
)
async def order_video(
    object_id: uuid.UUID, current: Auth, request: Request, db: DbSession
) -> ProjectOut:
    p, access = await _editable(db, current, object_id)
    if not ext.video_enabled():
        raise Problem(409, "video-off", "Ролики подключим, когда будет готов контент-завод")
    mats = {str(m.id): m for m in await service.active_materials(db)}
    est = await service.estimate(db, p)
    brief = {
        "project_id": str(p.id),
        "version": p.version,
        "style": p.style,
        "rooms": [service.room_brief(p, Room(**r), mats) for r in p.rooms],
        "total_rub": est.total_kop // 100,
    }
    try:
        p.video_job_id = await ext.order_video(brief)
    except Exception:  # noqa: BLE001
        raise Problem(502, "video-failed", "Контент-завод не ответил, попробуйте позже") from None
    p.video_status, p.video_url, p.video_requested_at = "processing", None, utcnow()
    audit(
        db,
        entity_type="design_project",
        entity_id=p.id,
        action="design.video_ordered",
        actor_id=current.id,
        ip=client_ip(request),
    )
    await db.commit()
    return await _out(db, p, access, current)
