"""Показатели пилота против целей Gate 1 (ТЗ 2.3, раздел 1.3)."""

import uuid
from datetime import date, datetime, timedelta

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import DbSession, PlatformAdmin
from app.core.security import utcnow
from app.models import (
    AuditLog,
    MediaReport,
    Organization,
    Payment,
    PurchaseOrder,
    RepairObject,
)
from app.models.enums import ObjectStatus, OrgType, PaymentStatus, PurchaseOrderStatus
from app.modules.notifications.service import MSK, rub
from app.modules.payments.bank import get_bank

router = APIRouter(prefix="/admin", tags=["admin"])

IN_WORK = (ObjectStatus.active, ObjectStatus.dispute)
PILOT = (ObjectStatus.active, ObjectStatus.dispute, ObjectStatus.completed)
_PO = PurchaseOrderStatus
PLACED = (_PO.confirmed, _PO.ordered, _PO.delivering, _PO.partially_delivered, _PO.delivered)


class Metric(BaseModel):
    key: str
    title: str
    value: float
    target: float
    unit: str
    ok: bool
    note: str | None = None


class ObjectReport(BaseModel):
    object_id: uuid.UUID
    address: str
    contractor: str | None
    status: ObjectStatus
    since: date
    working_days: int
    days_with_reports: int
    last_report_at: datetime | None


class KpiOut(BaseModel):
    metrics: list[Metric]
    objects: list[ObjectReport]
    generated_at: datetime


def working_days(start: date, end: date) -> list[date]:
    """Рабочие дни (пн–пт) с start по end включительно."""
    days, d = [], start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


async def _object_reports(db: AsyncSession, today: date) -> list[ObjectReport]:
    objects = (
        await db.execute(
            select(RepairObject, Organization.legal_name)
            .outerjoin(Organization, Organization.id == RepairObject.contractor_org_id)
            .where(RepairObject.status.in_(PILOT), RepairObject.deleted_at.is_(None))
            .order_by(RepairObject.created_at)
        )
    ).all()
    if not objects:
        return []
    ids = [o.id for o, _ in objects]
    # Начало пилота на объекте — когда он стал активным (резерв первого этапа).
    activated = dict(
        (
            await db.execute(
                select(AuditLog.entity_id, func.min(AuditLog.created_at))
                .where(AuditLog.action == "object.activated", AuditLog.entity_id.in_(ids))
                .group_by(AuditLog.entity_id)
            )
        ).all()
    )
    day = func.date(func.timezone("Europe/Moscow", MediaReport.received_server_at))
    report_days: dict[uuid.UUID, set[date]] = {i: set() for i in ids}
    for object_id, d in (
        await db.execute(
            select(MediaReport.object_id, day).where(MediaReport.object_id.in_(ids)).distinct()
        )
    ).all():
        report_days[object_id].add(d)
    last = dict(
        (
            await db.execute(
                select(MediaReport.object_id, func.max(MediaReport.received_server_at))
                .where(MediaReport.object_id.in_(ids))
                .group_by(MediaReport.object_id)
            )
        ).all()
    )
    out = []
    for obj, contractor in objects:
        since = (activated.get(obj.id) or obj.created_at).astimezone(MSK).date()
        end = today
        if obj.status == ObjectStatus.completed:
            end = min(today, obj.updated_at.astimezone(MSK).date())
        work = working_days(since, end)
        out.append(
            ObjectReport(
                object_id=obj.id,
                address=obj.address_text,
                contractor=contractor,
                status=obj.status,
                since=since,
                working_days=len(work),
                days_with_reports=len(report_days[obj.id] & set(work)),
                last_report_at=last.get(obj.id),
            )
        )
    return out


async def collect_kpi(db: AsyncSession) -> KpiOut:
    now = utcnow()
    objects = await _object_reports(db, now.astimezone(MSK).date())
    in_work = sum(1 for o in objects if o.status in IN_WORK)
    contractors = await db.scalar(
        select(func.count(func.distinct(RepairObject.contractor_org_id)))
        .join(Organization, Organization.id == RepairObject.contractor_org_id)
        .where(
            RepairObject.status.in_(PILOT),
            Organization.type == OrgType.contractor,
            Organization.verified_at.is_not(None),
        )
    )
    total_days = sum(o.working_days for o in objects)
    reported = sum(o.days_with_reports for o in objects)
    ratio = round(100 * reported / total_days, 1) if total_days else 0.0
    cycles = await db.scalar(
        select(func.count())
        .select_from(Payment)
        .where(
            Payment.stage_id.is_not(None),
            Payment.act_id.is_not(None),
            Payment.status == PaymentStatus.released,
        )
    )
    baskets = await db.scalar(
        select(func.count()).select_from(PurchaseOrder).where(PurchaseOrder.status.in_(PLACED))
    )
    margin = await db.scalar(
        select(
            func.coalesce(func.sum(PurchaseOrder.total_kop - PurchaseOrder.supplier_cost_kop), 0)
        ).where(PurchaseOrder.status == _PO.delivered)
    )
    fake_bank = get_bank().name == "fake"
    metrics = [
        Metric(
            key="objects",
            title="Пилотных объектов в работе",
            value=in_work,
            target=8,
            unit="шт.",
            ok=in_work >= 8,
        ),
        Metric(
            key="contractors",
            title="Подрядчиков подключено",
            value=contractors or 0,
            target=5,
            unit="шт.",
            ok=(contractors or 0) >= 5,
            note="Проверенные подрядчики, у которых есть объект в пилоте.",
        ),
        Metric(
            key="report_days",
            title="Рабочих дней с фотоотчётом",
            value=ratio,
            target=85,
            unit="%",
            ok=total_days > 0 and ratio >= 85,
            note=f"{reported} из {total_days} рабочих дней (пн–пт) с момента резерва.",
        ),
        Metric(
            key="cycles",
            title="Полных циклов «резерв → акт → выплата»",
            value=cycles or 0,
            target=3,
            unit="шт.",
            ok=(cycles or 0) >= 3 and not fake_bank,
            note="Пока банк — имитация: на реальных деньгах циклов ещё нет." if fake_bank else None,
        ),
        Metric(
            key="baskets",
            title="Корзин материалов через консьерж-закупки",
            value=baskets or 0,
            target=5,
            unit="шт.",
            ok=(baskets or 0) >= 5,
            note=f"Фактическая маржа по доставленным: {rub(margin or 0)}",
        ),
    ]
    return KpiOut(metrics=metrics, objects=objects, generated_at=now)


@router.get("/kpi", response_model=KpiOut, summary="Показатели пилота (Gate 1)")
async def kpi(_: PlatformAdmin, db: DbSession) -> KpiOut:
    return await collect_kpi(db)
