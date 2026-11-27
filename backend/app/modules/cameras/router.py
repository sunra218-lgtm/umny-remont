"""Камеры на объекте.

* Бригада или заказчик подключают камеру: IP-камеру (ссылка на снимок) или старый телефон
  (ссылка, которую открывают на телефоне — он сам шлёт кадры).
* Включает и выключает съёмку только заказчик в своём кабинете; при включении он даёт
  согласие на съёмку. Администратор может только выключить.
* Снимаем только в рабочие часы, которые задаёт заказчик.
* Смотрят таймлапс заказчик, бригада, инженер технадзора и администратор.
"""

import uuid
from datetime import date, datetime, time, timedelta
from typing import Literal

from fastapi import APIRouter, File, Header, Query, Request, UploadFile
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select

from app.core.access import ObjectAccess, load_object
from app.core.audit import audit
from app.core.config import get_settings
from app.core.deps import Auth, CurrentUser, DbSession, client_ip
from app.core.ids import uuid7
from app.core.problems import Problem
from app.core.security import sha256_hex, utcnow
from app.core.storage import signed_file_url
from app.models import Camera, CameraFrame, RepairObject, Stage
from app.models.enums import AssignmentRole, OrgRole
from app.modules.cameras import service
from app.modules.notifications import service as notes
from app.modules.notifications.service import MSK

router = APIRouter(tags=["cameras"])

CONSENT_TEXT = (
    "Я согласен на съёмку объекта камерой в рабочие часы. Кадры видят только участники "
    "ремонта, хранятся на серверах в России и удаляются через {days} дней. "
    "Выключить камеру можно в любой момент."
)


# ---------- права ----------


def can_watch(access: ObjectAccess) -> bool:
    return (
        access.is_customer
        or access.is_admin
        or bool(access.contractor_roles)
        or bool(access.assignment_roles)
    )


def can_edit(access: ObjectAccess) -> bool:
    return access.is_customer or access.is_admin or access.is_crew


def is_engineer(current: CurrentUser, access: ObjectAccess) -> bool:
    return (
        current.has_platform_role(OrgRole.platform_admin, OrgRole.inspector)
        or AssignmentRole.inspector in access.assignment_roles
    )


async def _camera(
    db: DbSession, current: CurrentUser, camera_id: uuid.UUID
) -> tuple[Camera, ObjectAccess]:
    cam = await db.get(Camera, camera_id)
    if cam is None or cam.deleted_at is not None:
        raise Problem(404, "not-found", "Камера не найдена")
    access = await load_object(db, current, cam.object_id)
    if not can_watch(access):
        raise Problem(404, "not-found", "Камера не найдена")
    return cam, access


# ---------- выдача ----------


class CameraOut(BaseModel):
    id: uuid.UUID
    object_id: uuid.UUID
    title: str
    kind: str
    snapshot_url: str | None  # без логина и пароля
    interval_min: int
    hour_from: int
    hour_to: int
    enabled: bool
    consent_at: datetime | None
    state: str
    last_frame_at: datetime | None
    last_seen_at: datetime | None
    last_error: str | None
    latest_frame_url: str | None
    frames_today: int
    can_toggle: bool
    can_edit: bool
    # Только сразу после создания или смены ссылки: открыть на телефоне-камере.
    phone_link: str | None = None


async def _out(
    db: DbSession,
    cam: Camera,
    access: ObjectAccess,
    current: CurrentUser,
    phone_link: str | None = None,
) -> CameraOut:
    now = utcnow()
    day_start = datetime.combine(now.astimezone(MSK).date(), time(0), MSK)
    latest = (
        await db.execute(
            select(CameraFrame.id)
            .where(CameraFrame.camera_id == cam.id)
            .order_by(CameraFrame.taken_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    today = await db.scalar(
        select(func.count())
        .select_from(CameraFrame)
        .where(CameraFrame.camera_id == cam.id, CameraFrame.taken_at >= day_start)
    )
    return CameraOut(
        id=cam.id,
        object_id=cam.object_id,
        title=cam.title,
        kind=cam.kind,
        snapshot_url=service.public_url(cam.snapshot_url),
        interval_min=cam.interval_min,
        hour_from=cam.hour_from,
        hour_to=cam.hour_to,
        enabled=cam.enabled,
        consent_at=cam.consent_at,
        state=service.state(cam, access.obj.status, now),
        last_frame_at=cam.last_frame_at,
        last_seen_at=cam.last_seen_at,
        last_error=cam.last_error,
        latest_frame_url=signed_file_url("frame", latest, current.id) if latest else None,
        frames_today=today or 0,
        can_toggle=access.is_customer,
        can_edit=can_edit(access),
        phone_link=phone_link,
    )


class CamerasOut(BaseModel):
    items: list[CameraOut]
    consent_text: str


def consent_text() -> str:
    return CONSENT_TEXT.format(days=get_settings().camera_retention_days)


@router.get("/objects/{object_id}/cameras", response_model=CamerasOut, summary="Камеры объекта")
async def list_cameras(object_id: uuid.UUID, current: Auth, db: DbSession) -> CamerasOut:
    access = await load_object(db, current, object_id)
    if not can_watch(access):
        return CamerasOut(items=[], consent_text=consent_text())
    cams = (
        await db.execute(
            select(Camera)
            .where(Camera.object_id == object_id, Camera.deleted_at.is_(None))
            .order_by(Camera.created_at)
        )
    ).scalars()
    return CamerasOut(
        items=[await _out(db, c, access, current) for c in cams], consent_text=consent_text()
    )


@router.get("/cameras/{camera_id}", response_model=CameraOut, summary="Камера")
async def get_camera(camera_id: uuid.UUID, current: Auth, db: DbSession) -> CameraOut:
    cam, access = await _camera(db, current, camera_id)
    return await _out(db, cam, access, current)


# ---------- подключение и настройка ----------


class Hours(BaseModel):
    hour_from: int = Field(default=9, ge=0, le=23)
    hour_to: int = Field(default=20, ge=1, le=24)

    @model_validator(mode="after")
    def _order(self) -> "Hours":
        if self.hour_from >= self.hour_to:
            raise ValueError("Начало рабочих часов должно быть раньше конца")
        return self


class CameraIn(Hours):
    title: str = Field(min_length=2, max_length=100)
    kind: Literal["ip", "phone"]
    snapshot_url: str | None = Field(default=None, max_length=500)
    interval_min: int = Field(default=15, ge=5, le=60)


def _phone_link(token: str) -> str:
    # Ключ — после «#»: он не уходит на сервер в адресе страницы и не попадает в журналы.
    return notes.url(f"/camera#t={token}")


async def _check_url(url: str | None) -> str:
    if not url:
        raise Problem(422, "snapshot-url-required", "Укажите ссылку на снимок с камеры")
    try:
        await service.check_snapshot_url(url)
    except service.BadCameraUrl as exc:
        raise Problem(422, "bad-snapshot-url", str(exc)) from None
    return url


@router.post(
    "/objects/{object_id}/cameras",
    response_model=CameraOut,
    status_code=201,
    summary="Подключить камеру",
)
async def create_camera(
    object_id: uuid.UUID, body: CameraIn, current: Auth, request: Request, db: DbSession
) -> CameraOut:
    access = await load_object(db, current, object_id)
    if not can_edit(access):
        raise Problem(403, "forbidden", "Камеру подключают заказчик или бригада")
    count = await db.scalar(
        select(func.count())
        .select_from(Camera)
        .where(Camera.object_id == object_id, Camera.deleted_at.is_(None))
    )
    if (count or 0) >= 4:
        raise Problem(409, "too-many-cameras", "На объекте уже 4 камеры")
    cam = Camera(
        id=uuid7(),
        object_id=object_id,
        title=body.title.strip(),
        kind=body.kind,
        interval_min=body.interval_min,
        hour_from=body.hour_from,
        hour_to=body.hour_to,
        created_by=current.id,
    )
    token = None
    if body.kind == "ip":
        cam.snapshot_url = await _check_url(body.snapshot_url)
    else:
        token = service.new_token()
        cam.token_hash = sha256_hex(token)
    db.add(cam)
    await db.flush()
    audit(
        db,
        entity_type="camera",
        entity_id=cam.id,
        action="camera.created",
        actor_id=current.id,
        ip=client_ip(request),
        new={"kind": cam.kind, "title": cam.title},
    )
    if not access.is_customer:
        notes.notify(
            db,
            [access.obj.customer_id],
            "camera_added",
            address=access.obj.address_text,
            url=notes.url(f"/objects/{object_id}#cameras"),
        )
    await db.commit()
    return await _out(db, cam, access, current, _phone_link(token) if token else None)


class CameraPatch(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=100)
    snapshot_url: str | None = Field(default=None, max_length=500)
    interval_min: int | None = Field(default=None, ge=5, le=60)
    hours: Hours | None = None


@router.patch("/cameras/{camera_id}", response_model=CameraOut, summary="Настроить камеру")
async def patch_camera(
    camera_id: uuid.UUID, body: CameraPatch, current: Auth, request: Request, db: DbSession
) -> CameraOut:
    cam, access = await _camera(db, current, camera_id)
    if not can_edit(access):
        raise Problem(403, "forbidden", "Настраивают заказчик или бригада")
    old = {
        "title": cam.title,
        "interval": cam.interval_min,
        "from": cam.hour_from,
        "to": cam.hour_to,
    }
    if body.title is not None:
        cam.title = body.title.strip()
    if body.interval_min is not None:
        cam.interval_min = body.interval_min
    if body.snapshot_url is not None:
        if cam.kind != "ip":
            raise Problem(422, "not-ip-camera", "Ссылка на снимок нужна только IP-камере")
        cam.snapshot_url = await _check_url(body.snapshot_url)
        cam.last_error = cam.last_error_at = None
    if body.hours is not None:
        # Рабочие часы — граница съёмки в квартире заказчика: их меняет только он.
        if not (access.is_customer or access.is_admin):
            raise Problem(403, "forbidden", "Часы съёмки задаёт заказчик")
        cam.hour_from, cam.hour_to = body.hours.hour_from, body.hours.hour_to
    audit(
        db,
        entity_type="camera",
        entity_id=cam.id,
        action="camera.updated",
        actor_id=current.id,
        ip=client_ip(request),
        old=old,
        new={
            "title": cam.title,
            "interval": cam.interval_min,
            "from": cam.hour_from,
            "to": cam.hour_to,
        },
    )
    await db.commit()
    return await _out(db, cam, access, current)


@router.post(
    "/cameras/{camera_id}/phone-link",
    response_model=CameraOut,
    summary="Новая ссылка для телефона-камеры (старая перестаёт работать)",
)
async def new_phone_link(
    camera_id: uuid.UUID, current: Auth, request: Request, db: DbSession
) -> CameraOut:
    cam, access = await _camera(db, current, camera_id)
    if not can_edit(access):
        raise Problem(403, "forbidden", "Ссылку выдают заказчик или бригада")
    if cam.kind != "phone":
        raise Problem(422, "not-phone-camera", "Ссылка нужна только телефону-камере")
    token = service.new_token()
    cam.token_hash = sha256_hex(token)
    audit(
        db,
        entity_type="camera",
        entity_id=cam.id,
        action="camera.link_rotated",
        actor_id=current.id,
        ip=client_ip(request),
    )
    await db.commit()
    return await _out(db, cam, access, current, _phone_link(token))


class EnableIn(BaseModel):
    consent: bool


@router.post("/cameras/{camera_id}/enable", response_model=CameraOut, summary="Включить камеру")
async def enable_camera(
    camera_id: uuid.UUID, body: EnableIn, current: Auth, request: Request, db: DbSession
) -> CameraOut:
    cam, access = await _camera(db, current, camera_id)
    if not access.is_customer:
        raise Problem(403, "forbidden", "Камеру включает заказчик")
    if not body.consent:
        raise Problem(422, "consent-required", "Без согласия на съёмку камеру не включить")
    if not cam.enabled:
        cam.enabled = True
        cam.consent_at, cam.consent_by = utcnow(), current.id
        audit(
            db,
            entity_type="camera",
            entity_id=cam.id,
            action="camera.enabled",
            actor_id=current.id,
            ip=client_ip(request),
            new={"consent_text": consent_text(), "hours": [cam.hour_from, cam.hour_to]},
        )
        await db.commit()
    return await _out(db, cam, access, current)


@router.post("/cameras/{camera_id}/disable", response_model=CameraOut, summary="Выключить камеру")
async def disable_camera(
    camera_id: uuid.UUID, current: Auth, request: Request, db: DbSession
) -> CameraOut:
    cam, access = await _camera(db, current, camera_id)
    if not (access.is_customer or access.is_admin):
        raise Problem(403, "forbidden", "Камеру выключает заказчик")
    if cam.enabled:
        cam.enabled = False
        audit(
            db,
            entity_type="camera",
            entity_id=cam.id,
            action="camera.disabled",
            actor_id=current.id,
            ip=client_ip(request),
        )
        await db.commit()
    return await _out(db, cam, access, current)


@router.delete("/cameras/{camera_id}", status_code=204, summary="Отключить камеру от объекта")
async def delete_camera(
    camera_id: uuid.UUID, current: Auth, request: Request, db: DbSession
) -> None:
    cam, access = await _camera(db, current, camera_id)
    if not can_edit(access):
        raise Problem(403, "forbidden", "Удаляют заказчик или бригада")
    cam.deleted_at, cam.enabled, cam.token_hash = utcnow(), False, None
    audit(
        db,
        entity_type="camera",
        entity_id=cam.id,
        action="camera.deleted",
        actor_id=current.id,
        ip=client_ip(request),
    )
    await db.commit()


# ---------- таймлапс ----------


class FrameOut(BaseModel):
    id: uuid.UUID
    taken_at: datetime
    url: str
    changed: bool
    stage_id: uuid.UUID | None
    stage_title: str | None
    hidden_work: bool
    labels: dict  # type: ignore[type-arg]
    labeled_by: str


class DayOut(BaseModel):
    day: date
    frames: int
    changed: int


class FramesOut(BaseModel):
    day: date
    days: list[DayOut]
    frames: list[FrameOut]
    can_label: bool


def _frame_out(f: CameraFrame, titles: dict[uuid.UUID, str], viewer: uuid.UUID) -> FrameOut:
    return FrameOut(
        id=f.id,
        taken_at=f.taken_at,
        url=signed_file_url("frame", f.id, viewer),
        changed=f.changed,
        stage_id=f.stage_id,
        stage_title=titles.get(f.stage_id) if f.stage_id else None,
        hidden_work=f.hidden_work,
        labels=f.labels,
        labeled_by=f.labeled_by,
    )


async def _stage_titles(db: DbSession, object_id: uuid.UUID) -> dict[uuid.UUID, str]:
    rows = await db.execute(select(Stage.id, Stage.title).where(Stage.object_id == object_id))
    return {i: t for i, t in rows.all()}


@router.get("/cameras/{camera_id}/frames", response_model=FramesOut, summary="Кадры за день")
async def frames(
    camera_id: uuid.UUID,
    current: Auth,
    db: DbSession,
    day: date | None = Query(default=None),
    hidden_only: bool = Query(default=False),
) -> FramesOut:
    cam, access = await _camera(db, current, camera_id)
    msk_day = func.date(func.timezone("Europe/Moscow", CameraFrame.taken_at))
    days = [
        DayOut(day=d, frames=n, changed=c or 0)
        for d, n, c in (
            await db.execute(
                select(
                    msk_day,
                    func.count(),
                    func.count().filter(CameraFrame.changed.is_(True)),
                )
                .where(CameraFrame.camera_id == cam.id)
                .group_by(msk_day)
                .order_by(msk_day.desc())
                .limit(60)
            )
        ).all()
    ]
    if day is None:
        day = days[0].day if days else utcnow().astimezone(MSK).date()
    start = datetime.combine(day, time(0), MSK)
    stmt = (
        select(CameraFrame)
        .where(
            CameraFrame.camera_id == cam.id,
            CameraFrame.taken_at >= start,
            CameraFrame.taken_at < start + timedelta(days=1),
        )
        .order_by(CameraFrame.taken_at)
        .limit(500)
    )
    if hidden_only:
        stmt = stmt.where(CameraFrame.hidden_work.is_(True))
    titles = await _stage_titles(db, cam.object_id)
    rows = (await db.execute(stmt)).scalars()
    return FramesOut(
        day=day,
        days=days,
        frames=[_frame_out(f, titles, current.id) for f in rows],
        can_label=is_engineer(current, access),
    )


class LabelIn(BaseModel):
    stage_id: uuid.UUID | None = None
    hidden_work: bool
    note: str | None = Field(default=None, max_length=300)


@router.patch("/camera-frames/{frame_id}", response_model=FrameOut, summary="Разметить кадр")
async def label_frame(
    frame_id: uuid.UUID, body: LabelIn, current: Auth, request: Request, db: DbSession
) -> FrameOut:
    frame = await db.get(CameraFrame, frame_id)
    if frame is None:
        raise Problem(404, "not-found", "Кадр не найден")
    access = await load_object(db, current, frame.object_id)
    if not is_engineer(current, access):
        raise Problem(403, "forbidden", "Размечает инженер технадзора")
    if body.stage_id is not None:
        stage = await db.get(Stage, body.stage_id)
        if stage is None or stage.object_id != frame.object_id:
            raise Problem(422, "stage-not-found", "Этап не из этого объекта")
    old = {"stage_id": frame.stage_id, "hidden_work": frame.hidden_work}
    frame.stage_id, frame.hidden_work = body.stage_id, body.hidden_work
    frame.labels = {**frame.labels, "note": body.note} if body.note else frame.labels
    frame.labeled_by, frame.labeled_user = "engineer", current.id
    audit(
        db,
        entity_type="camera_frame",
        entity_id=frame.id,
        action="camera_frame.labeled",
        actor_id=current.id,
        ip=client_ip(request),
        old=old,
        new={"stage_id": body.stage_id, "hidden_work": body.hidden_work},
    )
    await db.commit()
    return _frame_out(frame, await _stage_titles(db, frame.object_id), current.id)


# ---------- телефон-камера (без входа, по ключу из ссылки) ----------


async def _by_token(db: DbSession, token: str | None) -> Camera:
    if not token or len(token) > 100:
        raise Problem(401, "bad-camera-token", "Ссылка камеры не подходит")
    cam = (
        await db.execute(
            select(Camera).where(
                Camera.token_hash == sha256_hex(token), Camera.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if cam is None:
        raise Problem(
            401,
            "bad-camera-token",
            "Ссылка камеры не подходит",
            "Попросите новую ссылку в карточке объекта.",
        )
    return cam


class PhoneStatus(BaseModel):
    title: str
    enabled: bool
    state: str
    message: str
    interval_min: int
    hour_from: int
    hour_to: int
    next_in_s: int
    frames_today: int


STATE_TEXT = {
    "off": "Заказчик выключил камеру. Кадры не снимаются.",
    "paused": "Объект сейчас не в работе. Кадры не снимаются.",
    "sleeping": "Нерабочие часы. Камера проснётся сама.",
    "no_signal": "Камера снимает.",
    "live": "Камера снимает.",
}


async def _phone_status(db: DbSession, cam: Camera) -> PhoneStatus:
    now = utcnow()
    obj = await db.get(RepairObject, cam.object_id)
    st = service.state(cam, obj.status if obj else "cancelled", now)  # type: ignore[arg-type]
    day_start = datetime.combine(now.astimezone(MSK).date(), time(0), MSK)
    today = await db.scalar(
        select(func.count())
        .select_from(CameraFrame)
        .where(CameraFrame.camera_id == cam.id, CameraFrame.taken_at >= day_start)
    )
    return PhoneStatus(
        title=cam.title,
        enabled=cam.enabled,
        state=st,
        message=STATE_TEXT[st],
        interval_min=cam.interval_min,
        hour_from=cam.hour_from,
        hour_to=cam.hour_to,
        next_in_s=service.next_in_s(cam, now),
        frames_today=today or 0,
    )


@router.get("/camera/status", response_model=PhoneStatus, summary="Состояние телефона-камеры")
async def phone_status(
    db: DbSession, x_camera_token: str | None = Header(default=None)
) -> PhoneStatus:
    cam = await _by_token(db, x_camera_token)
    cam.last_seen_at = utcnow()
    await db.commit()
    return await _phone_status(db, cam)


@router.post("/camera/frames", response_model=PhoneStatus, summary="Кадр с телефона-камеры")
async def phone_frame(
    db: DbSession,
    photo: UploadFile = File(...),
    x_camera_token: str | None = Header(default=None),
) -> PhoneStatus:
    cam = await _by_token(db, x_camera_token)
    await db.refresh(cam, with_for_update=True)
    now = utcnow()
    cam.last_seen_at = now
    try:
        await service.can_capture(db, cam, now)
        data = await photo.read(get_settings().max_photo_bytes + 1)
        await service.save_frame(db, cam, data, now)
    except service.CaptureRefused as exc:
        await db.commit()
        status = {"not-an-image": 422, "file-too-large": 413, "too-early": 429}.get(exc.code, 409)
        raise Problem(status, exc.code, str(exc)) from None
    await db.commit()
    return await _phone_status(db, cam)
