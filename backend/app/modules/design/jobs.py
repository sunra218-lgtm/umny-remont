"""Фоновая работа умного дизайна: картинки комнат (YandexART) и статус роликов."""

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import uuid7
from app.core.security import utcnow
from app.core.storage import get_storage
from app.models import DesignProject, DesignRender
from app.modules.design import integrations as ext

log = logging.getLogger(__name__)

RENDER_TIMEOUT = timedelta(minutes=15)
VIDEO_TIMEOUT = timedelta(days=2)
BATCH = 10


async def process_renders(db: AsyncSession) -> int:
    """Запускает картинки в очереди и забирает готовые. Возвращает число готовых."""
    if not ext.renderer_enabled():
        return 0
    done = 0
    rows = (
        await db.execute(
            select(DesignRender)
            .where(DesignRender.status.in_(["queued", "running"]))
            .order_by(DesignRender.created_at)
            .limit(BATCH)
        )
    ).scalars()
    now = utcnow()
    for r in list(rows):
        try:
            if r.status == "queued":
                r.operation_id = await ext.start_render(r.prompt, seed=r.id.int % 1_000_000)
                r.status = "running"
            elif now - r.updated_at > RENDER_TIMEOUT:
                r.status, r.error = "failed", "Нейросеть не успела нарисовать"
            elif r.operation_id:
                image = await ext.poll_render(r.operation_id)
                if image is not None:
                    key = f"renders/{r.project_id}/{uuid7()}.jpg"
                    await get_storage().put(key, image, "image/jpeg")
                    r.storage_key, r.status = key, "done"
                    done += 1
        except ext.IntegrationError as exc:
            r.status, r.error = "failed", str(exc)[:300]
        except Exception as exc:  # noqa: BLE001 — сеть: попробуем на следующем круге
            log.warning("Картинка %s: %s", r.id, exc)
        await db.commit()
    return done


async def poll_videos(db: AsyncSession) -> int:
    if not ext.video_enabled():
        return 0
    ready = 0
    rows = (
        await db.execute(
            select(DesignProject).where(DesignProject.video_status == "processing").limit(BATCH)
        )
    ).scalars()
    now = utcnow()
    for p in list(rows):
        try:
            if p.video_requested_at and now - p.video_requested_at > VIDEO_TIMEOUT:
                p.video_status = "failed"
            elif p.video_job_id:
                status, url = await ext.video_status(p.video_job_id)
                if status == "done" and url:
                    p.video_status, p.video_url = "done", url
                    ready += 1
                elif status == "failed":
                    p.video_status = "failed"
        except Exception as exc:  # noqa: BLE001
            log.warning("Ролик проекта %s: %s", p.id, exc)
        await db.commit()
    return ready
