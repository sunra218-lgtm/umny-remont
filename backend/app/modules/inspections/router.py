"""Ход этапа, чек-лист этапа, выезды технадзора на стоп-точки и проверка фото
(ТЗ 2.3, разделы 4, 5 и 7: «Технадзор»)."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select

from app.core.access import ObjectAccess, load_object
from app.core.audit import audit
from app.core.deps import Auth, CurrentUser, DbSession, client_ip
from app.core.pagination import Page
from app.core.problems import Problem
from app.core.security import utcnow
from app.domain.statuses import ensure_transition
from app.models import (
    ChecklistItem,
    Inspection,
    MediaReport,
    Organization,
    OrgMember,
    RepairObject,
    Stage,
    User,
)
from app.models.enums import (
    AssignmentRole,
    InspectionResult,
    ObjectStatus,
    OrgRole,
    OrgType,
    ReviewStatus,
    StageStatus,
)
from app.modules.checklists.router import items_of, template_for_stage_type
from app.modules.objects.router import _stage_out, ensure_assignment
from app.modules.objects.schemas import StageOut

router = APIRouter(tags=["inspections"])


# ---------- общие проверки ----------


async def _stage(
    db: DbSession, current: CurrentUser, stage_id: uuid.UUID, *, lock: bool = False
) -> tuple[Stage, ObjectAccess]:
    stage = await db.get(Stage, stage_id, with_for_update=lock)
    if stage is None or stage.deleted_at is not None:
        raise Problem(404, "not-found", "Этап не найден")
    return stage, await load_object(db, current, stage.object_id)


def _is_crew(access: ObjectAccess) -> bool:
    return (
        access.is_admin
        or AssignmentRole.foreman in access.assignment_roles
        or OrgRole.contractor_admin in access.contractor_roles
    )


def _is_reviewer(access: ObjectAccess) -> bool:
    return access.is_admin or AssignmentRole.inspector in access.assignment_roles


# ---------- чек-лист этапа ----------


class ChecklistRow(BaseModel):
    id: uuid.UUID
    seq: int
    title: str
    required_angle: str | None
    photo_required: bool
    photos: int


class StageChecklist(BaseModel):
    stage_id: uuid.UUID
    template_title: str | None
    items: list[ChecklistRow]
    missing: list[uuid.UUID]


async def stage_checklist(db: DbSession, stage: Stage) -> StageChecklist:
    template = await template_for_stage_type(db, stage.stage_type)
    if template is None:
        return StageChecklist(stage_id=stage.id, template_title=None, items=[], missing=[])
    items = await items_of(db, template.id)
    counts = dict(
        (
            await db.execute(
                select(MediaReport.checklist_item_id, func.count())
                .where(
                    MediaReport.stage_id == stage.id,
                    MediaReport.checklist_item_id.in_([i.id for i in items]),
                    MediaReport.review_status != ReviewStatus.rejected,
                )
                .group_by(MediaReport.checklist_item_id)
            )
        ).all()
    )
    rows = [
        ChecklistRow(
            id=i.id,
            seq=i.seq,
            title=i.title,
            required_angle=i.required_angle,
            photo_required=i.photo_required,
            photos=counts.get(i.id, 0),
        )
        for i in items
    ]
    return StageChecklist(
        stage_id=stage.id,
        template_title=template.title,
        items=rows,
        missing=[r.id for r in rows if r.photo_required and r.photos == 0],
    )


@router.get(
    "/stages/{stage_id}/checklist",
    response_model=StageChecklist,
    summary="Чек-лист этапа и что уже снято",
)
async def get_checklist(stage_id: uuid.UUID, current: Auth, db: DbSession) -> StageChecklist:
    stage, _ = await _stage(db, current, stage_id)
    return await stage_checklist(db, stage)


async def checklist_item_ok(db: DbSession, stage: Stage, item_id: uuid.UUID) -> bool:
    template = await template_for_stage_type(db, stage.stage_type)
    item = await db.get(ChecklistItem, item_id)
    return template is not None and item is not None and item.template_id == template.id


class StageDetail(BaseModel):
    stage: StageOut
    object_id: uuid.UUID
    object_status: ObjectStatus
    address: str
    can_work: bool
    can_review: bool
    is_admin: bool
    is_customer: bool


@router.get("/stages/{stage_id}", response_model=StageDetail, summary="Карточка этапа")
async def get_stage(stage_id: uuid.UUID, current: Auth, db: DbSession) -> StageDetail:
    stage, access = await _stage(db, current, stage_id)
    return StageDetail(
        stage=_stage_out(stage),
        object_id=access.obj.id,
        object_status=access.obj.status,
        address=access.obj.address_text,
        can_work=_is_crew(access),
        can_review=_is_reviewer(access),
        is_admin=access.is_admin,
        is_customer=access.is_customer,
    )


# ---------- ход этапа ----------


class SubmitOut(BaseModel):
    stage: StageOut
    missing: list[str]


def _change_stage(
    db: DbSession,
    request: Request,
    current: CurrentUser,
    stage: Stage,
    target: StageStatus,
    action: str,
    **extra: object,
) -> None:
    ensure_transition("stage", stage.status, target)
    old = stage.status
    stage.status = target
    audit(
        db,
        entity_type="stage",
        entity_id=stage.id,
        action=action,
        actor_id=current.id,
        ip=client_ip(request),
        old={"status": old},
        new={"status": target, **extra},
    )


@router.post("/stages/{stage_id}/start", response_model=StageOut, summary="Начать работы на этапе")
async def start_stage(
    stage_id: uuid.UUID, current: Auth, request: Request, db: DbSession
) -> StageOut:
    stage, access = await _stage(db, current, stage_id, lock=True)
    if not _is_crew(access):
        raise Problem(403, "forbidden", "Работы начинает прораб или руководитель подрядчика")
    if stage.status in (StageStatus.planned, StageStatus.funding):
        raise Problem(
            409,
            "stage-not-funded",
            "Деньги на этап ещё не зарезервированы",
            "Начать работы можно после резерва.",
        )
    _change_stage(db, request, current, stage, StageStatus.in_progress, "stage.started")
    await db.commit()
    return _stage_out(stage)


@router.post("/stages/{stage_id}/submit", response_model=SubmitOut, summary="Сдать этап")
async def submit_stage(
    stage_id: uuid.UUID, current: Auth, request: Request, db: DbSession
) -> SubmitOut:
    """Этап со стоп-точкой уходит на проверку технадзора, без неё — сразу на приёмку заказчику.
    Несделанные ракурсы не блокируют сдачу, но видны проверяющему (ТЗ 6.2)."""
    stage, access = await _stage(db, current, stage_id, lock=True)
    if not _is_crew(access):
        raise Problem(403, "forbidden", "Этап сдаёт прораб или руководитель подрядчика")
    checklist = await stage_checklist(db, stage)
    missing = [r.title for r in checklist.items if r.id in checklist.missing]
    target = StageStatus.inspection if stage.is_stop_point else StageStatus.acceptance
    _change_stage(db, request, current, stage, target, "stage.submitted", missing_items=missing)
    await db.commit()
    return SubmitOut(stage=_stage_out(stage), missing=missing)


# ---------- выезды технадзора ----------


class InspectionCreate(BaseModel):
    inspector_id: uuid.UUID
    scheduled_at: datetime | None = None


class InspectionResultIn(BaseModel):
    result: InspectionResult
    remarks: str | None = Field(default=None, max_length=5000)

    @model_validator(mode="after")
    def remarks_required(self) -> "InspectionResultIn":
        if self.result != InspectionResult.passed and not (self.remarks or "").strip():
            raise ValueError("Опишите замечания: что нужно исправить")
        return self


class InspectionOut(BaseModel):
    id: uuid.UUID
    stage_id: uuid.UUID
    stage_title: str
    stage_status: StageStatus
    object_id: uuid.UUID
    address: str
    inspector_id: uuid.UUID
    inspector_name: str
    scheduled_at: datetime | None
    result: InspectionResult | None
    remarks: str | None
    signed_at: datetime | None
    created_at: datetime


def _inspection_select():  # type: ignore[no-untyped-def]
    return (
        select(Inspection, Stage, RepairObject, User)
        .join(Stage, Stage.id == Inspection.stage_id)
        .join(RepairObject, RepairObject.id == Stage.object_id)
        .join(User, User.id == Inspection.inspector_id)
    )


def _inspection_out(i: Inspection, s: Stage, o: RepairObject, u: User) -> InspectionOut:
    return InspectionOut(
        id=i.id,
        stage_id=s.id,
        stage_title=s.title,
        stage_status=s.status,
        object_id=o.id,
        address=o.address_text,
        inspector_id=u.id,
        inspector_name=u.full_name or u.phone,
        scheduled_at=i.scheduled_at,
        result=i.result,
        remarks=i.remarks,
        signed_at=i.signed_at,
        created_at=i.created_at,
    )


async def _load_inspection(db: DbSession, inspection_id: uuid.UUID) -> InspectionOut:
    row = (await db.execute(_inspection_select().where(Inspection.id == inspection_id))).one()
    return _inspection_out(*row)


@router.post(
    "/stages/{stage_id}/inspections",
    response_model=InspectionOut,
    status_code=201,
    summary="Назначить выезд технадзора",
)
async def create_inspection(
    stage_id: uuid.UUID, body: InspectionCreate, current: Auth, request: Request, db: DbSession
) -> InspectionOut:
    stage, access = await _stage(db, current, stage_id, lock=True)
    if not access.is_admin:
        raise Problem(403, "forbidden", "Выезд назначает администратор платформы")
    if not stage.is_stop_point:
        raise Problem(409, "not-a-stop-point", "У этапа нет стоп-точки")
    if stage.status in (StageStatus.accepted, StageStatus.paid):
        raise Problem(409, "stage-closed", "Этап уже принят")
    is_inspector = (
        await db.execute(
            select(OrgMember.id)
            .join(Organization, Organization.id == OrgMember.org_id)
            .where(
                OrgMember.user_id == body.inspector_id,
                OrgMember.role == OrgRole.inspector,
                Organization.type == OrgType.platform,
            )
        )
    ).first()
    if is_inspector is None:
        raise Problem(422, "not-an-inspector", "Это не инженер технадзора платформы")
    open_one = await db.scalar(
        select(Inspection.id).where(Inspection.stage_id == stage.id, Inspection.result.is_(None))
    )
    if open_one:
        raise Problem(409, "inspection-open", "По этапу уже назначен выезд")
    await ensure_assignment(db, stage.object_id, body.inspector_id, AssignmentRole.inspector)
    inspection = Inspection(
        stage_id=stage.id, inspector_id=body.inspector_id, scheduled_at=body.scheduled_at
    )
    db.add(inspection)
    await db.flush()
    audit(
        db,
        entity_type="inspection",
        entity_id=inspection.id,
        action="inspection.scheduled",
        actor_id=current.id,
        ip=client_ip(request),
        new=body.model_dump(mode="json"),
    )
    await db.commit()
    return await _load_inspection(db, inspection.id)


@router.get(
    "/stages/{stage_id}/inspections", response_model=list[InspectionOut], summary="Проверки этапа"
)
async def stage_inspections(
    stage_id: uuid.UUID, current: Auth, db: DbSession
) -> list[InspectionOut]:
    await _stage(db, current, stage_id)
    rows = await db.execute(
        _inspection_select().where(Inspection.stage_id == stage_id).order_by(Inspection.id.desc())
    )
    return [_inspection_out(*r) for r in rows.all()]


@router.get("/inspections", response_model=Page[InspectionOut], summary="Мои выезды")
async def my_inspections(
    current: Auth,
    db: DbSession,
    open_only: bool = True,
    cursor: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> Page[InspectionOut]:
    """Инженер видит свои выезды, администратор — все."""
    stmt = _inspection_select()
    if not current.is_platform_admin:
        stmt = stmt.where(Inspection.inspector_id == current.id)
    if open_only:
        stmt = stmt.where(Inspection.result.is_(None))
    if cursor:
        stmt = stmt.where(Inspection.id < cursor)
    rows = (await db.execute(stmt.order_by(Inspection.id.desc()).limit(limit + 1))).all()
    items = [_inspection_out(*r) for r in rows[:limit]]
    return Page(items=items, next_cursor=str(items[-1].id) if len(rows) > limit else None)


@router.post(
    "/inspections/{inspection_id}/result",
    response_model=InspectionOut,
    summary="Результат проверки",
)
async def inspection_result(
    inspection_id: uuid.UUID,
    body: InspectionResultIn,
    current: Auth,
    request: Request,
    db: DbSession,
) -> InspectionOut:
    inspection = await db.get(Inspection, inspection_id, with_for_update=True)
    if inspection is None:
        raise Problem(404, "not-found", "Проверка не найдена")
    if inspection.inspector_id != current.id:
        raise Problem(403, "forbidden", "Результат вносит инженер, назначенный на выезд")
    if inspection.result is not None:
        raise Problem(409, "inspection-closed", "Результат уже внесён")
    stage = await db.get(Stage, inspection.stage_id, with_for_update=True)
    assert stage is not None
    if stage.status != StageStatus.inspection:
        raise Problem(409, "stage-not-submitted", "Прораб ещё не сдал этап на проверку")
    target = (
        StageStatus.acceptance
        if body.result == InspectionResult.passed
        else StageStatus.in_progress
    )
    inspection.result = body.result
    inspection.remarks = (body.remarks or "").strip() or None
    inspection.signed_at = utcnow()
    audit(
        db,
        entity_type="inspection",
        entity_id=inspection.id,
        action="inspection.signed",
        actor_id=current.id,
        ip=client_ip(request),
        new=body.model_dump(mode="json"),
    )
    _change_stage(
        db,
        request,
        current,
        stage,
        target,
        "stage.inspected",
        result=body.result,
        inspection_id=inspection.id,
    )
    await db.commit()
    return await _load_inspection(db, inspection.id)


# ---------- проверка фото ----------


class ReviewIn(BaseModel):
    status: ReviewStatus
    comment: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def comment_for_reject(self) -> "ReviewIn":
        if self.status == ReviewStatus.pending:
            raise ValueError("Выберите: принять или отклонить")
        if self.status == ReviewStatus.rejected and not (self.comment or "").strip():
            raise ValueError("Напишите, что не так с фото")
        return self


class ReviewOut(BaseModel):
    id: uuid.UUID
    review_status: ReviewStatus
    review_comment: str | None
    reviewed_at: datetime | None


@router.post(
    "/media/{media_id}/review", response_model=ReviewOut, summary="Принять или отклонить фото"
)
async def review_media(
    media_id: uuid.UUID, body: ReviewIn, current: Auth, request: Request, db: DbSession
) -> ReviewOut:
    media = await db.get(MediaReport, media_id, with_for_update=True)
    if media is None:
        raise Problem(404, "not-found", "Фото не найдено")
    access = await load_object(db, current, media.object_id)
    if not _is_reviewer(access):
        raise Problem(403, "forbidden", "Фото проверяет инженер технадзора")
    old = {"review_status": media.review_status, "review_comment": media.review_comment}
    media.review_status = body.status
    media.review_comment = (body.comment or "").strip() or None
    media.reviewed_by = current.id
    media.reviewed_at = utcnow()
    audit(
        db,
        entity_type="media_report",
        entity_id=media.id,
        action="media.reviewed",
        actor_id=current.id,
        ip=client_ip(request),
        old=old,
        new={"review_status": body.status, "review_comment": media.review_comment},
    )
    await db.commit()
    return ReviewOut(
        id=media.id,
        review_status=media.review_status,
        review_comment=media.review_comment,
        reviewed_at=media.reviewed_at,
    )
