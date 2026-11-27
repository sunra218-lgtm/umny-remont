"""Показатели пилота против целей Gate 1 (ТЗ 2.3, раздел 1.3)."""

import csv
import io
import uuid
from datetime import date, datetime, timedelta

from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import DbSession, PlatformAdmin
from app.core.security import utcnow
from app.models import (
    AuditLog,
    ContractorReview,
    MarketBid,
    MarketTask,
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


class ModeStats(BaseModel):
    """Сравнение режимов поиска подрядчика: подбор платформой и маркетплейс."""

    mode: str
    title: str
    objects: int
    with_contractor: int
    contractor_pct: float
    hours_to_contractor: float | None  # медиана от создания объекта до выбора подрядчика
    reached_work: int
    reached_work_pct: float
    bids_per_task: float | None
    tasks_without_bids: int | None
    avg_rating: float | None
    reviews: int


class KpiOut(BaseModel):
    metrics: list[Metric]
    objects: list[ObjectReport]
    modes: list[ModeStats] = []
    generated_at: datetime


MODE_TITLES = {"platform": "Подбор платформой", "market": "Маркетплейс"}
WORKED = (ObjectStatus.active, ObjectStatus.dispute, ObjectStatus.completed)


async def _mode_stats(db: AsyncSession) -> list[ModeStats]:
    objs = (
        await db.execute(
            select(
                RepairObject.id,
                RepairObject.match_mode,
                RepairObject.status,
                RepairObject.contractor_org_id,
                RepairObject.created_at,
            ).where(RepairObject.deleted_at.is_(None))
        )
    ).all()
    chosen = dict(
        (
            await db.execute(
                select(AuditLog.entity_id, func.min(AuditLog.created_at))
                .where(AuditLog.action == "object.contractor_set")
                .group_by(AuditLog.entity_id)
            )
        ).all()
    )
    bids = dict(
        (
            await db.execute(
                select(MarketTask.object_id, func.count(MarketBid.id))
                .outerjoin(
                    MarketBid,
                    (MarketBid.task_id == MarketTask.id) & (MarketBid.status != "withdrawn"),
                )
                .group_by(MarketTask.object_id)
            )
        ).all()
    )
    ratings = {
        oid: r
        for oid, r in (
            await db.execute(select(ContractorReview.object_id, ContractorReview.rating))
        ).all()
    }
    out = []
    for mode, title in MODE_TITLES.items():
        rows = [o for o in objs if o.match_mode == mode]
        with_c = [o for o in rows if o.contractor_org_id is not None]
        hours = sorted(
            (chosen[o.id] - o.created_at).total_seconds() / 3600
            for o in with_c
            if o.id in chosen and chosen[o.id] >= o.created_at
        )
        median = None
        if hours:
            mid = len(hours) // 2
            median = hours[mid] if len(hours) % 2 else (hours[mid - 1] + hours[mid]) / 2
        worked = [o for o in rows if o.status in WORKED]
        tasks = [bids[o.id] for o in rows if o.id in bids] if mode == "market" else None
        rated = [ratings[o.id] for o in rows if o.id in ratings]
        n = len(rows)
        out.append(
            ModeStats(
                mode=mode,
                title=title,
                objects=n,
                with_contractor=len(with_c),
                contractor_pct=round(100 * len(with_c) / n, 1) if n else 0.0,
                hours_to_contractor=round(median, 1) if median is not None else None,
                reached_work=len(worked),
                reached_work_pct=round(100 * len(worked) / n, 1) if n else 0.0,
                bids_per_task=round(sum(tasks) / len(tasks), 1) if tasks else None,
                tasks_without_bids=sum(1 for t in tasks if t == 0) if tasks is not None else None,
                avg_rating=round(sum(rated) / len(rated), 2) if rated else None,
                reviews=len(rated),
            )
        )
    return out


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
    return KpiOut(metrics=metrics, objects=objects, modes=await _mode_stats(db), generated_at=now)


@router.get("/kpi", response_model=KpiOut, summary="Показатели пилота (Gate 1)")
async def kpi(_: PlatformAdmin, db: DbSession) -> KpiOut:
    return await collect_kpi(db)


STATUS_RU = {
    ObjectStatus.active: "в работе",
    ObjectStatus.dispute: "спор",
    ObjectStatus.completed: "завершён",
}


def kpi_csv(data: KpiOut) -> str:
    """Отчёт для таблицы (Excel): разделитель «;», дробная часть через запятую."""
    buf = io.StringIO()
    out = csv.writer(buf, delimiter=";", lineterminator="\r\n")

    def num(value: float) -> str:
        return (f"{value:g}").replace(".", ",")

    generated = data.generated_at.astimezone(MSK).strftime("%d.%m.%Y %H:%M")
    out.writerow(["Отчёт пилота «Умный ремонт» — цели Gate 1", f"на {generated} МСК"])
    out.writerow([])
    out.writerow(["Показатель", "Факт", "Цель", "Ед.", "Выполнено", "Пояснение"])
    for m in data.metrics:
        out.writerow(
            [m.title, num(m.value), num(m.target), m.unit, "да" if m.ok else "нет", m.note or ""]
        )
    out.writerow([])
    out.writerow(
        [
            "Объект",
            "Подрядчик",
            "Статус",
            "В пилоте с",
            "Рабочих дней",
            "Дней с фотоотчётом",
            "Доля, %",
            "Последний отчёт",
        ]
    )
    for o in data.objects:
        share = round(100 * o.days_with_reports / o.working_days) if o.working_days else 0
        last = ""
        if o.last_report_at:
            last = o.last_report_at.astimezone(MSK).strftime("%d.%m.%Y %H:%M")
        out.writerow(
            [
                o.address,
                o.contractor or "",
                STATUS_RU.get(o.status, o.status.value),
                o.since.strftime("%d.%m.%Y"),
                o.working_days,
                o.days_with_reports,
                share,
                last,
            ]
        )
    if data.modes:
        out.writerow([])
        out.writerow(
            [
                "Режим подбора",
                "Объектов",
                "Подрядчик найден",
                "Доля, %",
                "Часов до подрядчика (медиана)",
                "Дошли до работ",
                "Доля, %",
                "Откликов на заявку",
                "Заявок без откликов",
                "Средняя оценка",
            ]
        )
        for m in data.modes:
            out.writerow(
                [
                    m.title,
                    m.objects,
                    m.with_contractor,
                    num(m.contractor_pct),
                    num(m.hours_to_contractor) if m.hours_to_contractor is not None else "",
                    m.reached_work,
                    num(m.reached_work_pct),
                    num(m.bids_per_task) if m.bids_per_task is not None else "",
                    m.tasks_without_bids if m.tasks_without_bids is not None else "",
                    num(m.avg_rating) if m.avg_rating is not None else "",
                ]
            )
    return buf.getvalue()


@router.get(
    "/kpi.csv",
    summary="Отчёт пилота (Gate 1) для таблицы",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}}},
)
async def kpi_export(_: PlatformAdmin, db: DbSession) -> Response:
    data = await collect_kpi(db)
    name = f"gate1-{data.generated_at.astimezone(MSK):%Y-%m-%d}.csv"
    # BOM — чтобы Excel сразу открыл русский текст в правильной кодировке.
    return Response(
        content="\ufeff" + kpi_csv(data),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
