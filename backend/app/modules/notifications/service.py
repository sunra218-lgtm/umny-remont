"""Уведомления по СМС о ключевых событиях (ТЗ 2.3, разделы 4 и 6.1).

Команда только ставит уведомление в очередь (таблица notifications) в той же транзакции, что и
само событие: если событие откатилось, СМС не уйдёт. Отправляет очередь фоновый процесс
`python -m app.jobs worker`; он же раз в день в 18:00 напоминает бригадам об отчёте.
"""

import logging
import uuid
from collections.abc import Iterable
from datetime import timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import utcnow
from app.models import Notification, Organization, OrgMember, User
from app.models.enums import NotificationChannel, NotificationStatus, OrgRole, OrgType
from app.modules.auth.sms import get_sms_sender

log = logging.getLogger("umny.notify")

# Москва без перехода на летнее время с 2014 года.
MSK = timezone(timedelta(hours=3), "MSK")

TEMPLATES: dict[str, str] = {
    "hold_paid": "Деньги на этап «{stage}» зарезервированы, можно начинать работы. {address}",
    "stage_acceptance": "Этап «{stage}» готов к приёмке. Посмотрите фото: {url}",
    "inspection_scheduled": "Вам назначен выезд технадзора: {address}, {when}. {url}",
    "act_to_sign": "Подрядчик подписал акт по этапу «{stage}». Подпишите его: {url}",
    "payment_released": "Деньги за этап «{stage}» отправлены подрядчику. {address}",
    "dispute_opened": "Открыт спор по этапу «{stage}», выплата заморожена. {url}",
    "dispute_resolved": "Спор по этапу «{stage}» решён: {result}. {url}",
    "material_request": "Новая заявка на материалы: {address}. {url}",
    "order_proposed": "Корзина материалов на {amount} ждёт подтверждения 24 часа: {url}",
    "order_paid": "Заказчик оплатил материалы на {amount}, можно заказывать. {url}",
    "order_shipped": "Материалы по заказу отправлены на объект {address}. Примите доставку.",
    "delivery_problem": "Доставка на объект {address}: {result}. {url}",
    "order_delivered": "Материалы доставлены на объект {address}. {url}",
    "day_reported": "Бригада отчиталась о работах за {day}: {address}. Примите день: {url}",
    "day_returned": "Работы за {day} не приняты: {note}. {url}",
    "question_asked": "Вопрос бригады инженеру: {address}. Ответьте: {url}",
    "question_answered": "Инженер ответил на ваш вопрос: {url}",
    "camera_added": (
        "На объекте {address} подключили камеру. Она снимает, только если вы её включите: {url}"
    ),
    "daily_reminder": "Пришлите фотоотчёт за сегодня по объекту {address}: {url}",
}


def url(path: str) -> str:
    return f"{get_settings().public_base_url.rstrip('/')}{path}"


def rub(kop: int) -> str:
    return f"{kop // 100:,} ₽".replace(",", " ")


def render(template: str, payload: dict[str, Any]) -> str:
    return TEMPLATES[template].format(**payload)


def notify(
    db: AsyncSession, user_ids: Iterable[uuid.UUID | None], template: str, **payload: Any
) -> int:
    """Ставит СМС в очередь каждому получателю (без повторов). Возвращает число получателей."""
    render(template, payload)  # ошибка в шаблоне видна сразу, а не в фоне
    data = {k: str(v) for k, v in payload.items()}
    sent = 0
    for user_id in dict.fromkeys(u for u in user_ids if u is not None):
        db.add(
            Notification(
                user_id=user_id, channel=NotificationChannel.sms, template=template, payload=data
            )
        )
        sent += 1
    return sent


async def org_member_ids(
    db: AsyncSession, org_id: uuid.UUID | None, roles: Iterable[OrgRole]
) -> list[uuid.UUID]:
    if org_id is None:
        return []
    rows = await db.execute(
        select(OrgMember.user_id).where(OrgMember.org_id == org_id, OrgMember.role.in_(list(roles)))
    )
    return list(rows.scalars())


async def platform_staff_ids(db: AsyncSession, roles: Iterable[OrgRole]) -> list[uuid.UUID]:
    rows = await db.execute(
        select(OrgMember.user_id)
        .join(Organization, Organization.id == OrgMember.org_id)
        .where(Organization.type == OrgType.platform, OrgMember.role.in_(list(roles)))
    )
    return list(rows.scalars())


async def send_queued(db: AsyncSession, limit: int = 100) -> int:
    """Отправляет очередь. Несколько процессов не отправят одно СМС дважды (SKIP LOCKED)."""
    rows = (
        await db.execute(
            select(Notification, User.phone, User.blocked_at)
            .join(User, User.id == Notification.user_id)
            .where(Notification.status == NotificationStatus.queued)
            .order_by(Notification.created_at)
            .limit(limit)
            .with_for_update(of=Notification, skip_locked=True)
        )
    ).all()
    sender = get_sms_sender()
    for note, phone, blocked_at in rows:
        if blocked_at is not None:
            note.status, note.error = NotificationStatus.failed, "Пользователь заблокирован"
            continue
        try:
            await sender.send(phone, render(note.template, note.payload))
        except Exception as exc:  # noqa: BLE001 — ошибка провайдера не должна ронять очередь
            log.exception("СМС не отправлено")
            note.status, note.error = NotificationStatus.failed, str(exc)[:500]
        else:
            note.status, note.sent_at = NotificationStatus.sent, utcnow()
    await db.commit()
    return len(rows)
