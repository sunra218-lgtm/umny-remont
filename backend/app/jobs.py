"""Фоновые задачи: `python -m app.jobs worker` (постоянно) или разовый запуск одной задачи.

python -m app.jobs send       — отправить очередь СМС
python -m app.jobs expire     — закрыть корзины, не подтверждённые за 24 часа
python -m app.jobs reminders  — напомнить бригадам об отчёте за сегодня (обычно в 18:00)
python -m app.jobs cameras    — забрать кадры с IP-камер
python -m app.jobs purge      — удалить старые кадры камер
python -m app.jobs renders    — картинки комнат дизайн-проекта
python -m app.jobs videos     — статус роликов контент-завода
python -m app.jobs tracking   — отметки пути заказов мебели
python -m app.jobs curator    — проверки ИИ-куратора по объектам
python -m app.jobs worker     — всё вместе: очередь раз в 5 секунд, камеры раз в минуту,
                                напоминания в 18:00 МСК, чистка кадров раз в час
"""

import asyncio
import logging
import sys
import time as time_
from datetime import datetime, time

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import utcnow
from app.models import MediaReport, Notification, ObjectAssignment, RepairObject
from app.models.enums import AssignmentRole, ObjectStatus
from app.modules.cameras.service import capture_ip_cameras, purge_old_frames
from app.modules.design.jobs import poll_videos, process_renders
from app.modules.lux.jobs import poll_tracking
from app.modules.notifications import service as notes
from app.modules.notifications.service import MSK
from app.modules.procurement.router import expire_orders
from app.modules.soul.curator import run_checks

log = logging.getLogger("umny.jobs")
REMINDER_HOUR = 18


async def daily_reminders(db: AsyncSession, now: datetime | None = None) -> int:
    """Прорабам активных объектов, по которым сегодня ещё нет ни одного фото.
    В СМС — ссылка на приложение бригады, а не токен входа (ТЗ 6.1)."""
    now = now or utcnow()
    day = now.astimezone(MSK).date()
    day_start = datetime.combine(day, time(0), MSK)
    with_reports = (
        select(MediaReport.object_id)
        .where(MediaReport.received_server_at >= day_start)
        .group_by(MediaReport.object_id)
    )
    rows = await db.execute(
        select(ObjectAssignment.user_id, RepairObject.id, RepairObject.address_text)
        .join(RepairObject, RepairObject.id == ObjectAssignment.object_id)
        .where(
            ObjectAssignment.role == AssignmentRole.foreman,
            RepairObject.status == ObjectStatus.active,
            RepairObject.deleted_at.is_(None),
            RepairObject.id.notin_(with_reports),
        )
    )
    n = 0
    for user_id, object_id, address in rows.all():
        already = await db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.template == "daily_reminder",
                Notification.payload["object_id"].astext == str(object_id),
                Notification.payload["day"].astext == day.isoformat(),
            )
        )
        if already:
            continue
        n += notes.notify(
            db,
            [user_id],
            "daily_reminder",
            address=address,
            url=notes.url("/lite"),
            object_id=object_id,
            day=day.isoformat(),
        )
    await db.commit()
    return n


async def _with_db(job):  # type: ignore[no-untyped-def]
    async for db in get_session():
        return await job(db)


async def worker() -> None:
    log.warning("Фоновый процесс запущен")
    last: dict[str, float] = {}

    def every(name: str, seconds: int) -> bool:
        now = time_.monotonic()
        if now - last.get(name, -1e9) < seconds:
            return False
        last[name] = now
        return True

    while True:
        try:
            await _with_db(notes.send_queued)
            await _with_db(expire_orders)
            if utcnow().astimezone(MSK).hour == REMINDER_HOUR:
                await _with_db(daily_reminders)
            if every("cameras", 60):
                await _with_db(capture_ip_cameras)
            if every("purge", 3600):
                await _with_db(purge_old_frames)
            if every("renders", 10):
                await _with_db(process_renders)
            if every("videos", 60):
                await _with_db(poll_videos)
            if every("tracking", 1800):
                await _with_db(poll_tracking)
            if every("curator", 3600):
                await _with_db(run_checks)
        except Exception:
            log.exception("Ошибка фоновой задачи")
        await asyncio.sleep(5)


JOBS = {
    "send": notes.send_queued,
    "expire": expire_orders,
    "reminders": daily_reminders,
    "cameras": capture_ip_cameras,
    "purge": purge_old_frames,
    "renders": process_renders,
    "videos": poll_videos,
    "tracking": poll_tracking,
    "curator": run_checks,
}


def main(argv: list[str]) -> None:
    logging.basicConfig(level=logging.INFO)
    name = argv[1] if len(argv) > 1 else "worker"
    if name == "worker":
        asyncio.run(worker())
    elif name in JOBS:
        print(asyncio.run(_with_db(JOBS[name])))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
