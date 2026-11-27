"""Дневной план бригады и приёмка работ за день.

Утром прораб пишет план на день (что делаем, по каким этапам). Вечером отмечает, что сделано,
и сдаёт день. Заказчик или инженер технадзора принимает день или возвращает с замечанием.
Это дополняет приёмку по этапам: деньги по-прежнему уходят по акту этапа, а дневная
приёмка даёт заказчику ежедневный контроль. Фото за день видны рядом с планом.
"""

import uuid
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, func, select

from app.core.access import ObjectAccess, load_object
from app.core.audit import audit
from app.core.deps import Auth, DbSession, client_ip
from app.core.problems import Problem
from app.core.security import utcnow
from app.models import DailyPlan, DailyPlanItem, MediaReport, ObjectAssignment, Stage
from app.models.enums import AssignmentRole, ObjectStatus, OrgRole
from app.modules.notifications import service as notes
from app.modules.notifications.service import MSK

router = APIRouter(tags=["daily-plans"])

EDITABLE = ("planned", "returned")
WORKING = (ObjectStatus.contracting, ObjectStatus.active, ObjectStatus.dispute)


class ItemIn(BaseModel):
    title: str = Field(min_length=2, max_length=300)
    stage_id: uuid.UUID | None = None


class PlanIn(BaseModel):
    items: list[ItemIn] = Field(min_length=1, max_length=30)


class DoneIn(BaseModel):
    id: uuid.UUID
    done: bool
    note: str | None = Field(default=None, max_length=500)


class ReportIn(BaseModel):
    items: list[DoneIn]
    note: str | None = Field(default=None, max_length=2000)


class ReviewIn(BaseModel):
    accepted: bool
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _note(self) -> "ReviewIn":
        if not self.accepted and not (self.note or "").strip():
            raise ValueError("Напишите, что не так: бригада увидит замечание")
        return self


class ItemOut(BaseModel):
    id: uuid.UUID
    title: str
    stage_id: uuid.UUID | None
    stage_title: str | None
    done: bool
    note: str | None


class PlanOut(BaseModel):
    id: uuid.UUID
    object_id: uuid.UUID
    day: date
    status: str
    items: list[ItemOut]
    report_note: str | None
    reported_at: datetime | None
    review_note: str | None
    reviewed_at: datetime | None
    photos: int
    can_edit: bool
    can_review: bool


def today() -> date:
    return utcnow().astimezone(MSK).date()


def _is_crew(access: ObjectAccess) -> bool:
    return access.is_crew


def _is_reviewer(access: ObjectAccess) -> bool:
    return (
        access.is_customer or access.is_admin or AssignmentRole.inspector in access.assignment_roles
    )


async def _photos(db: DbSession, object_id: uuid.UUID, day: date) -> int:
    start = datetime.combine(day, datetime.min.time(), MSK)
    return int(
        await db.scalar(
            select(func.count())
            .select_from(MediaReport)
            .where(
                MediaReport.object_id == object_id,
                MediaReport.received_server_at >= start,
                MediaReport.received_server_at < start + timedelta(days=1),
            )
        )
        or 0
    )


async def plan_out(db: DbSession, plan: DailyPlan, access: ObjectAccess) -> PlanOut:
    rows = (
        await db.execute(
            select(DailyPlanItem, Stage.title)
            .outerjoin(Stage, Stage.id == DailyPlanItem.stage_id)
            .where(DailyPlanItem.plan_id == plan.id)
            .order_by(DailyPlanItem.seq)
        )
    ).all()
    return PlanOut(
        id=plan.id,
        object_id=plan.object_id,
        day=plan.day,
        status=plan.status,
        items=[
            ItemOut(
                id=i.id,
                title=i.title,
                stage_id=i.stage_id,
                stage_title=title,
                done=i.done,
                note=i.note,
            )
            for i, title in rows
        ],
        report_note=plan.report_note,
        reported_at=plan.reported_at,
        review_note=plan.review_note,
        reviewed_at=plan.reviewed_at,
        photos=await _photos(db, plan.object_id, plan.day),
        can_edit=_is_crew(access) and plan.status in EDITABLE,
        can_review=_is_reviewer(access) and plan.status == "reported",
    )


async def _plan(db: DbSession, current: Auth, plan_id: uuid.UUID) -> tuple[DailyPlan, ObjectAccess]:
    plan = await db.get(DailyPlan, plan_id, with_for_update=True)
    if plan is None:
        raise Problem(404, "not-found", "План не найден")
    return plan, await load_object(db, current, plan.object_id)


@router.get(
    "/objects/{object_id}/daily-plans",
    response_model=list[PlanOut],
    summary="Дневные планы объекта",
)
async def list_plans(
    object_id: uuid.UUID,
    current: Auth,
    db: DbSession,
    days: int = Query(default=14, ge=1, le=120),
) -> list[PlanOut]:
    access = await load_object(db, current, object_id)
    rows = (
        await db.execute(
            select(DailyPlan)
            .where(
                DailyPlan.object_id == object_id,
                DailyPlan.day >= today() - timedelta(days=days),
            )
            .order_by(DailyPlan.day.desc())
        )
    ).scalars()
    return [await plan_out(db, p, access) for p in rows]


@router.put(
    "/objects/{object_id}/daily-plans/{day}",
    response_model=PlanOut,
    summary="План работ на день",
)
async def put_plan(
    object_id: uuid.UUID,
    day: date,
    body: PlanIn,
    current: Auth,
    request: Request,
    db: DbSession,
) -> PlanOut:
    access = await load_object(db, current, object_id)
    if not _is_crew(access):
        raise Problem(403, "forbidden", "План пишет прораб или руководитель подрядчика")
    if access.obj.status not in WORKING:
        raise Problem(409, "object-not-in-work", "Объект не в работе")
    now = today()
    if not now - timedelta(days=1) <= day <= now + timedelta(days=7):
        raise Problem(
            422, "bad-day", "План пишется на сегодня или на неделю вперёд", "Вчерашний — до утра."
        )
    stage_ids = {i.stage_id for i in body.items if i.stage_id}
    if stage_ids:
        found = set(
            (
                await db.execute(
                    select(Stage.id).where(
                        Stage.id.in_(stage_ids),
                        Stage.object_id == object_id,
                        Stage.deleted_at.is_(None),
                    )
                )
            ).scalars()
        )
        if found != stage_ids:
            raise Problem(422, "stage-not-found", "Этап не из этого объекта")
    plan = (
        await db.execute(
            select(DailyPlan)
            .where(DailyPlan.object_id == object_id, DailyPlan.day == day)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if plan is None:
        plan = DailyPlan(object_id=object_id, day=day, created_by=current.id, status="planned")
        db.add(plan)
        await db.flush()
    elif plan.status not in EDITABLE:
        raise Problem(409, "plan-locked", "День уже сдан на приёмку")
    else:
        await db.execute(delete(DailyPlanItem).where(DailyPlanItem.plan_id == plan.id))
    for seq, item in enumerate(body.items, start=1):
        db.add(DailyPlanItem(plan_id=plan.id, seq=seq, title=item.title, stage_id=item.stage_id))
    audit(
        db,
        entity_type="daily_plan",
        entity_id=plan.id,
        action="daily_plan.saved",
        actor_id=current.id,
        ip=client_ip(request),
        new=body.model_dump(mode="json") | {"day": day.isoformat()},
    )
    await db.commit()
    return await plan_out(db, plan, access)


@router.post("/daily-plans/{plan_id}/report", response_model=PlanOut, summary="Сдать день")
async def report_day(
    plan_id: uuid.UUID, body: ReportIn, current: Auth, request: Request, db: DbSession
) -> PlanOut:
    plan, access = await _plan(db, current, plan_id)
    if not _is_crew(access):
        raise Problem(403, "forbidden", "День сдаёт прораб или руководитель подрядчика")
    if plan.status not in EDITABLE:
        raise Problem(409, "plan-locked", "День уже сдан на приёмку")
    if plan.day > today():
        raise Problem(409, "day-not-started", "Этот день ещё не наступил")
    items = {
        i.id: i
        for i in (
            await db.execute(select(DailyPlanItem).where(DailyPlanItem.plan_id == plan.id))
        ).scalars()
    }
    if {d.id for d in body.items} - items.keys():
        raise Problem(422, "item-not-found", "Пункт не из этого плана")
    for d in body.items:
        items[d.id].done = d.done
        items[d.id].note = (d.note or "").strip() or None
    plan.status = "reported"
    plan.report_note = (body.note or "").strip() or None
    plan.reported_at = utcnow()
    audit(
        db,
        entity_type="daily_plan",
        entity_id=plan.id,
        action="daily_plan.reported",
        actor_id=current.id,
        ip=client_ip(request),
        new=body.model_dump(mode="json"),
    )
    inspectors = (
        await db.execute(
            select(ObjectAssignment.user_id).where(
                ObjectAssignment.object_id == plan.object_id,
                ObjectAssignment.role == AssignmentRole.inspector,
            )
        )
    ).scalars()
    notes.notify(
        db,
        [access.obj.customer_id, *inspectors],
        "day_reported",
        day=plan.day.strftime("%d.%m"),
        address=access.obj.address_text,
        url=notes.url(f"/objects/{plan.object_id}#day"),
    )
    await db.commit()
    return await plan_out(db, plan, access)


@router.post("/daily-plans/{plan_id}/review", response_model=PlanOut, summary="Принять день")
async def review_day(
    plan_id: uuid.UUID, body: ReviewIn, current: Auth, request: Request, db: DbSession
) -> PlanOut:
    plan, access = await _plan(db, current, plan_id)
    if not _is_reviewer(access):
        raise Problem(403, "forbidden", "День принимает заказчик или инженер технадзора")
    if plan.status != "reported":
        raise Problem(409, "plan-not-reported", "Бригада ещё не сдала этот день")
    plan.status = "accepted" if body.accepted else "returned"
    plan.review_note = (body.note or "").strip() or None
    plan.reviewed_by, plan.reviewed_at = current.id, utcnow()
    audit(
        db,
        entity_type="daily_plan",
        entity_id=plan.id,
        action="daily_plan.accepted" if body.accepted else "daily_plan.returned",
        actor_id=current.id,
        ip=client_ip(request),
        new=body.model_dump(mode="json"),
    )
    if not body.accepted:
        bosses = await notes.org_member_ids(
            db, access.obj.contractor_org_id, [OrgRole.contractor_admin]
        )
        notes.notify(
            db,
            [plan.created_by, *bosses],
            "day_returned",
            day=plan.day.strftime("%d.%m"),
            note=plan.review_note,
            url=notes.url("/lite"),
        )
    await db.commit()
    return await plan_out(db, plan, access)
