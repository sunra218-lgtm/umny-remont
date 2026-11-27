"""Фотоотчёты бригады: код съёмки, загрузка с подписью устройства, лента объекта."""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, File, Form, Query, Request, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select, text

from app.core import device_crypto
from app.core.access import ObjectAccess, load_object
from app.core.audit import audit
from app.core.config import get_settings
from app.core.deps import Auth, CurrentUser, DbSession, client_ip
from app.core.ids import uuid7
from app.core.pagination import Page
from app.core.problems import Problem
from app.core.security import utcnow
from app.core.storage import check_media_link, get_storage, signed_media_url
from app.models import (
    CaptureChallenge,
    ChecklistItem,
    DeviceSession,
    MediaReport,
    Stage,
    User,
)
from app.models.enums import AssignmentRole, ObjectStatus, OrgRole, SessionKind
from app.modules.field.antifraud import NotAnImage, distance_m, inspect_image

router = APIRouter(tags=["field"])

# Без похожих букв и цифр (0/O, 1/I/L), чтобы код читался на фото.
CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
CLOSED = {ObjectStatus.cancelled, ObjectStatus.completed}


class ChallengeOut(BaseModel):
    challenge_id: uuid.UUID
    code: str
    issued_at: datetime
    expires_at: datetime
    ttl_s: int


class MediaOut(BaseModel):
    id: uuid.UUID
    object_id: uuid.UUID
    stage_id: uuid.UUID | None
    stage_title: str | None = None
    checklist_item_id: uuid.UUID | None
    author_name: str | None = None
    received_server_at: datetime
    captured_client_at: datetime | None
    checklist_item_title: str | None = None
    review_status: str
    review_comment: str | None = None
    fraud_flags: dict[str, Any] | None = None
    url: str


def _can_shoot(access: ObjectAccess) -> bool:
    return AssignmentRole.foreman in access.assignment_roles or (
        OrgRole.contractor_admin in access.contractor_roles
    )


async def _lite_session(db: DbSession, current: CurrentUser) -> DeviceSession:
    session = await db.get(DeviceSession, current.session_id)
    if session is None or session.kind != SessionKind.lite or not session.device_public_key:
        raise Problem(
            403,
            "lite-only",
            "Фотоотчёт снимается в приложении для бригады",
            "Откройте ссылку для входа на телефоне.",
        )
    return session


async def _stage_for_shooting(
    db: DbSession, current: CurrentUser, stage_id: uuid.UUID
) -> tuple[Stage, ObjectAccess]:
    stage = await db.get(Stage, stage_id)
    if stage is None or stage.deleted_at is not None:
        raise Problem(404, "not-found", "Этап не найден")
    access = await load_object(db, current, stage.object_id)
    if not _can_shoot(access):
        raise Problem(403, "forbidden", "Отчёт сдаёт прораб, назначенный на объект")
    if access.obj.status in CLOSED:
        raise Problem(409, "object-closed", "Объект закрыт")
    return stage, access


@router.post(
    "/stages/{stage_id}/capture-challenges",
    response_model=ChallengeOut,
    status_code=201,
    summary="Получить код съёмки",
)
async def create_challenge(stage_id: uuid.UUID, current: Auth, db: DbSession) -> ChallengeOut:
    session = await _lite_session(db, current)
    stage, access = await _stage_for_shooting(db, current, stage_id)
    ttl = get_settings().capture_ttl_s
    now = utcnow()
    challenge = CaptureChallenge(
        session_id=session.id,
        object_id=access.obj.id,
        stage_id=stage.id,
        code="".join(secrets.choice(CODE_ALPHABET) for _ in range(6)),
        issued_at=now,
        expires_at=now + timedelta(seconds=ttl),
    )
    db.add(challenge)
    await db.commit()
    return ChallengeOut(
        challenge_id=challenge.id,
        code=challenge.code,
        issued_at=now,
        expires_at=challenge.expires_at,
        ttl_s=ttl,
    )


async def _duplicate_flags(
    db: DbSession, sha256: str, phash: int, media_id: uuid.UUID
) -> dict[str, Any]:
    flags: dict[str, Any] = {}
    exact = (
        await db.execute(
            select(MediaReport.id, MediaReport.object_id)
            .where(MediaReport.sha256 == sha256, MediaReport.id != media_id)
            .limit(1)
        )
    ).first()
    if exact:
        flags["duplicate_exact"] = {"media_id": str(exact.id), "object_id": str(exact.object_id)}
        return flags
    similar = (
        await db.execute(
            text(
                "SELECT id, object_id, bit_count((phash # :p)::bit(64)) AS dist "
                "FROM media_reports WHERE phash IS NOT NULL AND id <> :id "
                "AND bit_count((phash # :p)::bit(64)) <= :max "
                "ORDER BY dist LIMIT 1"
            ),
            {"p": phash, "id": media_id, "max": get_settings().phash_max_distance},
        )
    ).first()
    if similar:
        flags["duplicate_similar"] = {
            "media_id": str(similar.id),
            "object_id": str(similar.object_id),
            "distance": int(similar.dist),
        }
    return flags


@router.post(
    "/stages/{stage_id}/media",
    response_model=MediaOut,
    status_code=201,
    summary="Загрузить фото отчёта",
)
async def upload_media(
    stage_id: uuid.UUID,
    current: Auth,
    request: Request,
    db: DbSession,
    file: UploadFile = File(...),
    signature: str = Form(..., description="ECDSA P-256 (r||s, base64) от «sha256:код»"),
    challenge_id: uuid.UUID | None = Form(default=None),
    checklist_item_id: uuid.UUID | None = Form(default=None),
    captured_client_at: datetime | None = Form(default=None),
    elapsed_ms: int | None = Form(default=None, ge=0),
    geo_lat: Decimal | None = Form(default=None, ge=-90, le=90),
    geo_lon: Decimal | None = Form(default=None, ge=-180, le=180),
    geo_accuracy_m: int | None = Form(default=None, ge=0),
) -> MediaOut:
    s = get_settings()
    session = await _lite_session(db, current)
    stage, access = await _stage_for_shooting(db, current, stage_id)
    obj = access.obj
    data = await file.read(s.max_photo_bytes + 1)
    if len(data) > s.max_photo_bytes:
        raise Problem(413, "file-too-large", "Фото слишком большое")
    try:
        info = inspect_image(data)
    except NotAnImage as exc:
        raise Problem(422, "not-an-image", str(exc)) from None
    sha256 = hashlib.sha256(data).hexdigest()
    now = utcnow()
    flags: dict[str, Any] = {}

    challenge: CaptureChallenge | None = None
    if challenge_id is not None:
        challenge = (
            await db.execute(
                select(CaptureChallenge)
                .where(CaptureChallenge.id == challenge_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if (
            challenge is None
            or challenge.session_id != session.id
            or challenge.stage_id != stage.id
        ):
            raise Problem(422, "challenge-not-found", "Код съёмки не найден для этого этапа")

    code = challenge.code if challenge else ""
    if not device_crypto.verify(
        session.device_public_key or "", f"{sha256}:{code}".encode(), signature
    ):
        raise Problem(
            400,
            "device-signature-invalid",
            "Подпись устройства не прошла",
            "Фото должно быть снято в приложении на этом телефоне.",
        )

    if challenge is None:
        flags["no_challenge"] = True
    else:
        if challenge.used_at is not None:
            flags["challenge_reused"] = True
        if challenge.expires_at < now:
            # Офлайн: кадр мог быть снят вовремя, а отправлен позже (ТЗ 6.2).
            if elapsed_ms is not None and elapsed_ms <= s.capture_ttl_s * 1000:
                flags["delayed_upload"] = {
                    "minutes_late": int((now - challenge.expires_at).total_seconds() // 60)
                }
            else:
                flags["challenge_expired"] = True
        if challenge.used_at is None:
            challenge.used_at = now

    if geo_lat is None or geo_lon is None:
        flags["no_geo"] = True
    elif obj.geo_lat is not None and obj.geo_lon is not None:
        if geo_accuracy_m is not None and geo_accuracy_m <= s.geo_min_accuracy_m:
            dist = distance_m(
                float(geo_lat), float(geo_lon), float(obj.geo_lat), float(obj.geo_lon)
            )
            if dist > s.geo_max_distance_m:
                flags["geo_mismatch"] = {"distance_m": int(dist)}

    if checklist_item_id is not None:
        from app.modules.inspections.router import checklist_item_ok

        if not await checklist_item_ok(db, stage, checklist_item_id):
            raise Problem(422, "checklist-item-not-found", "Пункт чек-листа не из этого этапа")

    media_id = uuid7()
    flags |= await _duplicate_flags(db, sha256, info.phash, media_id)
    key = f"objects/{obj.id}/stages/{stage.id}/{media_id}.{info.ext}"
    await get_storage().put(key, data, info.content_type)
    media = MediaReport(
        id=media_id,
        object_id=obj.id,
        stage_id=stage.id,
        checklist_item_id=checklist_item_id,
        author_id=current.id,
        s3_key=key,
        sha256=sha256,
        phash=info.phash,
        captured_client_at=captured_client_at,
        received_server_at=now,
        challenge_id=challenge.id if challenge else None,
        geo_lat=geo_lat,
        geo_lon=geo_lon,
        geo_accuracy_m=geo_accuracy_m,
        fraud_flags=flags,
    )
    db.add(media)
    audit(
        db,
        entity_type="media_report",
        entity_id=media_id,
        action="media.uploaded",
        actor_id=current.id,
        ip=client_ip(request),
        new={"stage_id": stage.id, "sha256": sha256, "flags": sorted(flags)},
    )
    await db.commit()
    return MediaOut(
        id=media.id,
        object_id=obj.id,
        stage_id=stage.id,
        stage_title=stage.title,
        checklist_item_id=checklist_item_id,
        author_name=current.user.full_name,
        received_server_at=now,
        captured_client_at=captured_client_at,
        review_status=media.review_status.value,
        fraud_flags=flags,
        url=signed_media_url(media.id, current.id),
    )


@router.get("/objects/{object_id}/feed", response_model=Page[MediaOut], summary="Лента отчётов")
async def feed(
    object_id: uuid.UUID,
    current: Auth,
    db: DbSession,
    stage_id: uuid.UUID | None = None,
    cursor: uuid.UUID | None = None,
    limit: int = Query(default=30, ge=1, le=100),
) -> Page[MediaOut]:
    access = await load_object(db, current, object_id)
    # Пометки антифрода видят проверяющие, а не заказчик.
    show_flags = not access.is_customer or access.is_admin
    stmt = (
        select(MediaReport, Stage.title, User.full_name, User.phone, ChecklistItem.title)
        .outerjoin(Stage, Stage.id == MediaReport.stage_id)
        .outerjoin(ChecklistItem, ChecklistItem.id == MediaReport.checklist_item_id)
        .join(User, User.id == MediaReport.author_id)
        .where(MediaReport.object_id == object_id)
    )
    if stage_id:
        stmt = stmt.where(MediaReport.stage_id == stage_id)
    if cursor:
        stmt = stmt.where(MediaReport.id < cursor)
    rows = (await db.execute(stmt.order_by(MediaReport.id.desc()).limit(limit + 1))).all()
    items = [
        MediaOut(
            id=m.id,
            object_id=m.object_id,
            stage_id=m.stage_id,
            stage_title=title,
            checklist_item_id=m.checklist_item_id,
            checklist_item_title=item_title,
            author_name=name or phone,
            review_comment=m.review_comment,
            received_server_at=m.received_server_at,
            captured_client_at=m.captured_client_at,
            review_status=m.review_status.value,
            fraud_flags=m.fraud_flags if show_flags else None,
            url=signed_media_url(m.id, current.id),
        )
        for m, title, name, phone, item_title in rows[:limit]
    ]
    next_cursor = str(rows[limit - 1][0].id) if len(rows) > limit else None
    return Page(items=items, next_cursor=next_cursor)


@router.get("/objects/{object_id}/feed/stats", summary="Сколько дней с отчётом")
async def feed_stats(object_id: uuid.UUID, current: Auth, db: DbSession) -> dict[str, int]:
    await load_object(db, current, object_id)
    days = await db.scalar(
        select(func.count(func.distinct(func.date(MediaReport.received_server_at)))).where(
            MediaReport.object_id == object_id
        )
    )
    total = await db.scalar(
        select(func.count()).select_from(MediaReport).where(MediaReport.object_id == object_id)
    )
    return {"days_with_reports": days or 0, "photos": total or 0}


@router.get(
    "/media/{media_id}/content", summary="Файл по подписанной ссылке", response_class=Response
)
async def media_content(
    media_id: uuid.UUID, exp: int, v: uuid.UUID, sig: str, db: DbSession
) -> Response:
    if not check_media_link(media_id, exp, v, sig):
        raise Problem(403, "link-expired", "Ссылка на фото устарела", "Обновите страницу.")
    media = await db.get(MediaReport, media_id)
    if media is None:
        raise Problem(404, "not-found", "Фото не найдено")
    data = await get_storage().get(media.s3_key)
    ext = media.s3_key.rsplit(".", 1)[-1]
    content_type = {"jpg": "image/jpeg", "webp": "image/webp", "png": "image/png"}.get(
        ext, "application/octet-stream"
    )
    return Response(
        data,
        media_type=content_type,
        headers={"Cache-Control": "private, max-age=600", "X-Content-Type-Options": "nosniff"},
    )
