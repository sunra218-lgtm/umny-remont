"""Кадры с камер: правила съёмки, сохранение, забор снимков с IP-камер и удаление старых.

Правила одни для IP-камеры и телефона:
* камера включена заказчиком (с согласием на съёмку);
* сейчас рабочие часы камеры по Москве;
* объект в работе (подписание договора, ремонт или спор);
* с прошлого кадра прошёл интервал.
"""

import asyncio
import ipaddress
import logging
import secrets
import socket
from datetime import datetime, timedelta
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.ids import uuid7
from app.core.security import utcnow
from app.core.storage import get_storage
from app.models import Camera, CameraFrame, RepairObject
from app.models.enums import ObjectStatus
from app.modules.cameras.recognition import get_recognizer
from app.modules.field.antifraud import NotAnImage, inspect_image
from app.modules.notifications.service import MSK

log = logging.getLogger(__name__)

WORKING = (ObjectStatus.contracting, ObjectStatus.active, ObjectStatus.dispute)
# Кадр считается «изменившимся», если отличается от прошлого сильнее, чем на столько бит.
CHANGE_BITS = 6
MAX_IP_PER_RUN = 50


def in_hours(camera: Camera, now: datetime) -> bool:
    hour = now.astimezone(MSK).hour
    return camera.hour_from <= hour < camera.hour_to


def state(camera: Camera, obj_status: ObjectStatus, now: datetime) -> str:
    """off — выключена заказчиком, paused — объект не в работе, sleeping — нерабочие часы,
    no_signal — давно нет кадров, live — снимает."""
    if not camera.enabled:
        return "off"
    if obj_status not in WORKING:
        return "paused"
    if not in_hours(camera, now):
        return "sleeping"
    grace = timedelta(minutes=camera.interval_min * 3)
    if camera.last_frame_at is None or now - camera.last_frame_at > grace:
        return "no_signal"
    return "live"


def due(camera: Camera, now: datetime) -> bool:
    tries = [t for t in (camera.last_frame_at, camera.last_error_at) if t is not None]
    if not tries:
        return True
    # Полминуты запаса, чтобы таймер телефона не упирался в отказ.
    return now - max(tries) >= timedelta(minutes=camera.interval_min, seconds=-30)


def next_in_s(camera: Camera, now: datetime) -> int:
    if camera.last_frame_at is None:
        return 0
    left = camera.last_frame_at + timedelta(minutes=camera.interval_min) - now
    return max(0, int(left.total_seconds()))


class CaptureRefused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


async def can_capture(db: AsyncSession, camera: Camera, now: datetime) -> None:
    if camera.deleted_at is not None:
        raise CaptureRefused("camera-removed", "Камера удалена")
    if not camera.enabled:
        raise CaptureRefused("camera-off", "Заказчик выключил камеру")
    obj = await db.get(RepairObject, camera.object_id)
    if obj is None or obj.deleted_at is not None or obj.status not in WORKING:
        raise CaptureRefused("object-not-in-work", "Объект сейчас не в работе")
    if not in_hours(camera, now):
        raise CaptureRefused("off-hours", "Сейчас нерабочие часы — камера не снимает")
    if not due(camera, now):
        raise CaptureRefused("too-early", "Слишком рано для следующего кадра")


async def save_frame(db: AsyncSession, camera: Camera, data: bytes, now: datetime) -> CameraFrame:
    """Проверяет, что это фото, сохраняет кадр и размечает его. Не коммитит."""
    if len(data) > get_settings().max_photo_bytes:
        raise CaptureRefused("file-too-large", "Кадр слишком большой")
    try:
        info = await asyncio.to_thread(inspect_image, data)
    except NotAnImage as exc:
        raise CaptureRefused("not-an-image", str(exc)) from None
    prev = (
        await db.execute(
            select(CameraFrame.phash)
            .where(CameraFrame.camera_id == camera.id)
            .order_by(CameraFrame.taken_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    changed = prev is None or bin((prev ^ info.phash) & (2**64 - 1)).count("1") > CHANGE_BITS
    frame_id = uuid7()
    key = (
        f"cameras/{camera.object_id}/{camera.id}/{now.astimezone(MSK):%Y%m%d}/{frame_id}.{info.ext}"
    )
    await get_storage().put(key, data, info.content_type)
    rec = await get_recognizer().recognize(db, camera, data, now)
    frame = CameraFrame(
        id=frame_id,
        camera_id=camera.id,
        object_id=camera.object_id,
        taken_at=now,
        storage_key=key,
        content_type=info.content_type,
        size_bytes=len(data),
        phash=info.phash,
        changed=changed,
        stage_id=rec.stage_id,
        hidden_work=rec.hidden_work,
        labels=rec.labels,
    )
    db.add(frame)
    camera.last_frame_at = now
    camera.last_seen_at = now
    camera.last_error = None
    return frame


# ---------- IP-камеры ----------


class BadCameraUrl(ValueError):
    pass


def _resolve(host: str) -> list[str]:
    return [a[4][0] for a in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)]


async def check_snapshot_url(url: str) -> None:
    """Ссылка на снимок должна вести во внешний интернет: сервер не ходит во внутреннюю сеть."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise BadCameraUrl("Нужна ссылка вида http://адрес-камеры/snapshot.jpg")
    if get_settings().env == "dev":
        return
    try:
        addrs = await asyncio.to_thread(_resolve, parts.hostname)
    except OSError:
        raise BadCameraUrl("Адрес камеры не найден") from None
    if not addrs or not all(ipaddress.ip_address(a.split("%")[0]).is_global for a in addrs):
        raise BadCameraUrl("Камера должна быть доступна из интернета, а не только из домашней сети")


def public_url(url: str | None) -> str | None:
    """Ссылка без логина и пароля — её можно показывать."""
    if not url:
        return None
    p = urlsplit(url)
    port = f":{p.port}" if p.port else ""
    return f"{p.scheme}://{p.hostname}{port}{p.path}"


async def fetch_snapshot(url: str, transport: httpx.AsyncBaseTransport | None = None) -> bytes:
    await check_snapshot_url(url)
    s = get_settings()
    p = urlsplit(url)
    auth = None
    if p.username:
        auth = httpx.DigestAuth(p.username, p.password or "")
        port = f":{p.port}" if p.port else ""
        url = p._replace(netloc=f"{p.hostname}{port}").geturl()
    async with httpx.AsyncClient(
        timeout=s.camera_fetch_timeout_s, transport=transport, follow_redirects=False
    ) as client:
        resp = await client.get(url, auth=auth)
        if resp.status_code == 401 and p.username:
            resp = await client.get(url, auth=httpx.BasicAuth(p.username, p.password or ""))
        resp.raise_for_status()
        if len(resp.content) > s.max_photo_bytes:
            raise CaptureRefused("file-too-large", "Снимок с камеры больше 2 МБ")
        return resp.content


async def capture_ip_cameras(
    db: AsyncSession,
    now: datetime | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> int:
    now = now or utcnow()
    cams = (
        await db.execute(
            select(Camera)
            .join(RepairObject, RepairObject.id == Camera.object_id)
            .where(
                Camera.kind == "ip",
                Camera.enabled.is_(True),
                Camera.deleted_at.is_(None),
                RepairObject.deleted_at.is_(None),
                RepairObject.status.in_(WORKING),
            )
            .order_by(Camera.last_frame_at.asc().nulls_first())
            .limit(MAX_IP_PER_RUN)
        )
    ).scalars()
    n = 0
    for cam in list(cams):
        if not in_hours(cam, now) or not due(cam, now) or not cam.snapshot_url:
            continue
        try:
            data = await fetch_snapshot(cam.snapshot_url, transport)
            await save_frame(db, cam, data, now)
            n += 1
        except (httpx.HTTPError, BadCameraUrl, CaptureRefused) as exc:
            # Не стучимся в недоступную камеру каждые 5 секунд: следующая попытка — через интервал.
            cam.last_error, cam.last_error_at = _error_text(exc), now
            log.info("Камера %s не ответила: %s", cam.id, exc)
        await db.commit()
    return n


def _error_text(exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "Камера не ответила вовремя"
    if isinstance(exc, httpx.HTTPStatusError):
        if exc.response.status_code in (401, 403):
            return "Камера не пускает: проверьте логин и пароль в ссылке"
        return f"Камера ответила ошибкой {exc.response.status_code}"
    if isinstance(exc, httpx.HTTPError):
        return "Камера недоступна из интернета"
    return str(exc)[:300]


# ---------- хранение ----------


async def purge_old_frames(db: AsyncSession, now: datetime | None = None, batch: int = 500) -> int:
    """Удаляет кадры старше ``camera_retention_days`` вместе с файлами."""
    now = now or utcnow()
    border = now - timedelta(days=get_settings().camera_retention_days)
    frames = (
        await db.execute(select(CameraFrame).where(CameraFrame.taken_at < border).limit(batch))
    ).scalars()
    n = 0
    storage = get_storage()
    for f in list(frames):
        try:
            await storage.delete(f.storage_key)
        except Exception:
            log.exception("Не удалось удалить файл кадра %s", f.id)
            continue
        await db.delete(f)
        n += 1
    await db.commit()
    return n


def new_token() -> str:
    return secrets.token_urlsafe(24)
