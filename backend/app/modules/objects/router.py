import uuid

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.access import load_object, visible_objects_query
from app.core.audit import audit
from app.core.deps import Auth, DbSession, client_ip
from app.core.pagination import Page
from app.core.problems import Problem
from app.domain.statuses import ensure_transition
from app.models import (
    EstimateItem,
    ObjectAssignment,
    Organization,
    OrgMember,
    RepairObject,
    Stage,
    User,
)
from app.models.enums import (
    AssignmentRole,
    ObjectStatus,
    OrgRole,
    OrgType,
    StageStatus,
)
from app.modules.auth.service import get_or_create_user
from app.modules.objects.schemas import (
    AssignmentCreate,
    AssignmentOut,
    ContractorSet,
    ObjectCreate,
    ObjectOut,
    ObjectUpdate,
    PersonOut,
    StageCreate,
    StageOut,
    StageUpdate,
)

router = APIRouter(tags=["objects"])

# Кого можно назначить на объект: роль назначения → роль сотрудника, которая для этого нужна.
ASSIGNMENT_SOURCE = {
    AssignmentRole.foreman: (OrgType.contractor, OrgRole.foreman),
    AssignmentRole.inspector: (OrgType.platform, OrgRole.inspector),
    AssignmentRole.procurement_manager: (OrgType.platform, OrgRole.procurement_manager),
}
EDITABLE_OBJECT_STATUSES = {ObjectStatus.draft, ObjectStatus.survey, ObjectStatus.estimating}


async def object_out(db: DbSession, obj: RepairObject, flags: dict[str, bool]) -> ObjectOut:
    customer = await db.get(User, obj.customer_id)
    org = await db.get(Organization, obj.contractor_org_id) if obj.contractor_org_id else None
    return ObjectOut(
        id=obj.id,
        status=obj.status,
        address_text=obj.address_text,
        address_fias=obj.address_fias,
        area_m2=obj.area_m2,
        geo_lat=obj.geo_lat,
        geo_lon=obj.geo_lon,
        customer=PersonOut(id=customer.id, phone=customer.phone, full_name=customer.full_name)
        if customer
        else None,
        contractor_org_id=obj.contractor_org_id,
        contractor_name=org.legal_name if org else None,
        created_at=obj.created_at,
        access=flags,
    )


@router.post("/objects", response_model=ObjectOut, status_code=201, summary="Создать объект")
async def create_object(
    body: ObjectCreate, current: Auth, request: Request, db: DbSession
) -> ObjectOut:
    customer_id = current.id
    if body.customer_phone:
        if not current.is_platform_admin:
            raise Problem(
                403, "forbidden", "Создать объект для другого заказчика может только админ"
            )
        customer, _ = await get_or_create_user(db, body.customer_phone)
        customer_id = customer.id
    obj = RepairObject(
        customer_id=customer_id,
        **body.model_dump(exclude={"customer_phone"}),
    )
    db.add(obj)
    await db.flush()
    audit(
        db,
        entity_type="object",
        entity_id=obj.id,
        action="object.created",
        actor_id=current.id,
        ip=client_ip(request),
        new=body.model_dump(exclude={"customer_phone"}) | {"customer_id": customer_id},
    )
    await db.commit()
    access = await load_object(db, current, obj.id)
    return await object_out(db, access.obj, access.flags())


@router.get("/objects", response_model=Page[ObjectOut], summary="Мои объекты")
async def list_objects(
    current: Auth,
    db: DbSession,
    status: ObjectStatus | None = None,
    cursor: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> Page[ObjectOut]:
    stmt = visible_objects_query(current)
    if status is not None:
        stmt = stmt.where(RepairObject.status == status)
    if cursor is not None:
        stmt = stmt.where(RepairObject.id < cursor)
    rows = list(
        (await db.execute(stmt.order_by(RepairObject.id.desc()).limit(limit + 1))).scalars()
    )
    items = []
    for obj in rows[:limit]:
        access = await load_object(db, current, obj.id)
        items.append(await object_out(db, obj, access.flags()))
    next_cursor = str(rows[limit - 1].id) if len(rows) > limit else None
    return Page(items=items, next_cursor=next_cursor)


@router.get("/objects/{object_id}", response_model=ObjectOut, summary="Карточка объекта")
async def get_object(object_id: uuid.UUID, current: Auth, db: DbSession) -> ObjectOut:
    access = await load_object(db, current, object_id)
    return await object_out(db, access.obj, access.flags())


@router.patch("/objects/{object_id}", response_model=ObjectOut, summary="Изменить адрес и площадь")
async def update_object(
    object_id: uuid.UUID, body: ObjectUpdate, current: Auth, request: Request, db: DbSession
) -> ObjectOut:
    access = await load_object(db, current, object_id, lock=True)
    if not (access.is_customer or access.is_admin):
        raise Problem(403, "forbidden", "Изменить объект может заказчик или администратор")
    obj = access.obj
    if obj.status not in EDITABLE_OBJECT_STATUSES and not access.is_admin:
        raise Problem(409, "object-locked", "После подписания договора адрес меняет только админ")
    changes = body.model_dump(exclude_unset=True)
    old = {k: getattr(obj, k) for k in changes}
    for key, value in changes.items():
        setattr(obj, key, value)
    if changes:
        audit(
            db,
            entity_type="object",
            entity_id=obj.id,
            action="object.updated",
            actor_id=current.id,
            ip=client_ip(request),
            old=old,
            new=changes,
        )
        await db.commit()
        await db.refresh(obj)
    return await object_out(db, obj, access.flags())


@router.post("/objects/{object_id}/cancel", response_model=ObjectOut, summary="Отменить объект")
async def cancel_object(
    object_id: uuid.UUID, current: Auth, request: Request, db: DbSession
) -> ObjectOut:
    access = await load_object(db, current, object_id, lock=True)
    if not (access.is_customer or access.is_admin):
        raise Problem(403, "forbidden", "Отменить объект может заказчик или администратор")
    obj = access.obj
    ensure_transition("object", obj.status, ObjectStatus.cancelled)
    old = obj.status
    obj.status = ObjectStatus.cancelled
    audit(
        db,
        entity_type="object",
        entity_id=obj.id,
        action="object.cancelled",
        actor_id=current.id,
        ip=client_ip(request),
        old={"status": old},
        new={"status": obj.status},
    )
    await db.commit()
    return await object_out(db, obj, access.flags())


@router.put(
    "/objects/{object_id}/contractor", response_model=ObjectOut, summary="Назначить подрядчика"
)
async def set_contractor(
    object_id: uuid.UUID, body: ContractorSet, current: Auth, request: Request, db: DbSession
) -> ObjectOut:
    access = await load_object(db, current, object_id, lock=True)
    if not access.is_admin:
        raise Problem(403, "forbidden", "Подрядчика назначает администратор платформы")
    obj = access.obj
    if obj.status in {ObjectStatus.active, ObjectStatus.completed, ObjectStatus.cancelled}:
        raise Problem(
            409,
            "object-locked",
            "Подрядчика нельзя сменить в этом статусе",
            "Замена подрядчика на активном объекте идёт через спор.",
        )
    if body.contractor_org_id is not None:
        org = await db.get(Organization, body.contractor_org_id)
        if org is None or org.deleted_at is not None or org.type != OrgType.contractor:
            raise Problem(422, "not-a-contractor", "Это не организация-подрядчик")
        if org.verified_at is None:
            raise Problem(
                409,
                "org-not-verified",
                "Сначала проверьте подрядчика",
                "Назначать можно только проверенные организации.",
            )
    old = obj.contractor_org_id
    obj.contractor_org_id = body.contractor_org_id
    audit(
        db,
        entity_type="object",
        entity_id=obj.id,
        action="object.contractor_set",
        actor_id=current.id,
        ip=client_ip(request),
        old={"contractor_org_id": old},
        new={"contractor_org_id": body.contractor_org_id},
    )
    await db.commit()
    access = await load_object(db, current, object_id)
    return await object_out(db, access.obj, access.flags())


# ---------- Назначения ----------


@router.get(
    "/objects/{object_id}/assignments",
    response_model=list[AssignmentOut],
    summary="Кто работает на объекте",
)
async def list_assignments(
    object_id: uuid.UUID, current: Auth, db: DbSession
) -> list[AssignmentOut]:
    await load_object(db, current, object_id)
    rows = await db.execute(
        select(ObjectAssignment, User)
        .join(User, User.id == ObjectAssignment.user_id)
        .where(ObjectAssignment.object_id == object_id)
        .order_by(ObjectAssignment.created_at)
    )
    return [
        AssignmentOut(
            id=a.id,
            role=a.role,
            created_at=a.created_at,
            user=PersonOut(id=u.id, phone=u.phone, full_name=u.full_name),
        )
        for a, u in rows
    ]


async def ensure_assignment(
    db: DbSession, object_id: uuid.UUID, user_id: uuid.UUID, role: AssignmentRole
) -> ObjectAssignment | None:
    """Создаёт назначение, если его ещё нет. Возвращает новое назначение или None."""
    exists = await db.execute(
        select(ObjectAssignment.id).where(
            ObjectAssignment.object_id == object_id,
            ObjectAssignment.user_id == user_id,
            ObjectAssignment.role == role,
        )
    )
    if exists.first() is not None:
        return None
    assignment = ObjectAssignment(object_id=object_id, user_id=user_id, role=role)
    db.add(assignment)
    await db.flush()
    return assignment


@router.post(
    "/objects/{object_id}/assignments",
    response_model=AssignmentOut,
    status_code=201,
    summary="Назначить на объект",
)
async def add_assignment(
    object_id: uuid.UUID, body: AssignmentCreate, current: Auth, request: Request, db: DbSession
) -> AssignmentOut:
    access = await load_object(db, current, object_id)
    obj = access.obj
    org_type, member_role = ASSIGNMENT_SOURCE[body.role]
    if body.role == AssignmentRole.foreman:
        if not access.can_manage_team:
            raise Problem(403, "forbidden", "Прораба назначает руководитель подрядчика")
        if obj.contractor_org_id is None:
            raise Problem(409, "no-contractor", "Сначала назначьте подрядчика на объект")
        org_filter = OrgMember.org_id == obj.contractor_org_id
    else:
        if not access.is_admin:
            raise Problem(403, "forbidden", "Эту роль назначает администратор платформы")
        org_filter = Organization.type == org_type
    member = (
        await db.execute(
            select(OrgMember.id)
            .join(Organization, Organization.id == OrgMember.org_id)
            .where(org_filter, OrgMember.user_id == body.user_id, OrgMember.role == member_role)
        )
    ).first()
    if member is None:
        raise Problem(422, "not-a-member", "У этого человека нет нужной роли в организации")
    assignment = await ensure_assignment(db, obj.id, body.user_id, body.role)
    if assignment is None:
        raise Problem(409, "assignment-exists", "Уже назначен на объект с этой ролью")
    audit(
        db,
        entity_type="object",
        entity_id=obj.id,
        action="object.assignment_added",
        actor_id=current.id,
        ip=client_ip(request),
        new={"user_id": body.user_id, "role": body.role},
    )
    await db.commit()
    user = await db.get(User, body.user_id)
    assert user is not None
    return AssignmentOut(
        id=assignment.id,
        role=assignment.role,
        created_at=assignment.created_at,
        user=PersonOut(id=user.id, phone=user.phone, full_name=user.full_name),
    )


@router.delete(
    "/objects/{object_id}/assignments/{assignment_id}", status_code=204, summary="Снять с объекта"
)
async def remove_assignment(
    object_id: uuid.UUID,
    assignment_id: uuid.UUID,
    current: Auth,
    request: Request,
    db: DbSession,
) -> Response:
    access = await load_object(db, current, object_id)
    assignment = await db.get(ObjectAssignment, assignment_id)
    if assignment is None or assignment.object_id != object_id:
        raise Problem(404, "not-found", "Назначение не найдено")
    allowed = access.is_admin or (
        assignment.role == AssignmentRole.foreman and access.can_manage_team
    )
    if not allowed:
        raise Problem(403, "forbidden", "Недостаточно прав, чтобы снять с объекта")
    await db.delete(assignment)
    audit(
        db,
        entity_type="object",
        entity_id=object_id,
        action="object.assignment_removed",
        actor_id=current.id,
        ip=client_ip(request),
        old={"user_id": assignment.user_id, "role": assignment.role},
    )
    await db.commit()
    return Response(status_code=204)


# ---------- Этапы ----------


def _stage_out(stage: Stage) -> StageOut:
    return StageOut.model_validate(stage, from_attributes=True)


async def next_stage_seq(db: DbSession, object_id: uuid.UUID) -> int:
    current_max = await db.scalar(select(func.max(Stage.seq)).where(Stage.object_id == object_id))
    return (current_max or 0) + 1


@router.get("/objects/{object_id}/stages", response_model=list[StageOut], summary="Этапы")
async def list_stages(object_id: uuid.UUID, current: Auth, db: DbSession) -> list[StageOut]:
    await load_object(db, current, object_id)
    rows = await db.execute(
        select(Stage)
        .where(Stage.object_id == object_id, Stage.deleted_at.is_(None))
        .order_by(Stage.seq)
    )
    return [_stage_out(s) for s in rows.scalars()]


@router.post(
    "/objects/{object_id}/stages", response_model=StageOut, status_code=201, summary="Добавить этап"
)
async def create_stage(
    object_id: uuid.UUID, body: StageCreate, current: Auth, request: Request, db: DbSession
) -> StageOut:
    access = await load_object(db, current, object_id, lock=True)
    if not access.can_edit_estimates:
        raise Problem(403, "forbidden", "Этапы составляет сметчик или подрядчик")
    data = body.model_dump()
    data["seq"] = data["seq"] or await next_stage_seq(db, object_id)
    stage = Stage(object_id=object_id, **data)
    db.add(stage)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise Problem(409, "stage-seq-taken", "Этап с таким номером уже есть") from None
    audit(
        db,
        entity_type="stage",
        entity_id=stage.id,
        action="stage.created",
        actor_id=current.id,
        ip=client_ip(request),
        new=data,
    )
    await db.commit()
    await db.refresh(stage)
    return _stage_out(stage)


async def _editable_stage(db: DbSession, current: Auth, stage_id: uuid.UUID) -> Stage:
    stage = await db.get(Stage, stage_id)
    if stage is None or stage.deleted_at is not None:
        raise Problem(404, "not-found", "Этап не найден")
    access = await load_object(db, current, stage.object_id)
    if not access.can_edit_estimates:
        raise Problem(403, "forbidden", "Этапы составляет сметчик или подрядчик")
    if stage.status != StageStatus.planned:
        raise Problem(409, "stage-locked", "Этап уже в работе, его нельзя изменить")
    return stage


@router.patch("/stages/{stage_id}", response_model=StageOut, summary="Изменить этап")
async def update_stage(
    stage_id: uuid.UUID, body: StageUpdate, current: Auth, request: Request, db: DbSession
) -> StageOut:
    stage = await _editable_stage(db, current, stage_id)
    changes = body.model_dump(exclude_unset=True)
    old = {k: getattr(stage, k) for k in changes}
    for key, value in changes.items():
        setattr(stage, key, value)
    if stage.planned_start and stage.planned_end and stage.planned_end < stage.planned_start:
        raise Problem(422, "validation-error", "Дата окончания этапа раньше даты начала")
    if changes:
        audit(
            db,
            entity_type="stage",
            entity_id=stage.id,
            action="stage.updated",
            actor_id=current.id,
            ip=client_ip(request),
            old=old,
            new=changes,
        )
        await db.commit()
        await db.refresh(stage)
    return _stage_out(stage)


@router.delete("/stages/{stage_id}", status_code=204, summary="Удалить этап")
async def delete_stage(
    stage_id: uuid.UUID, current: Auth, request: Request, db: DbSession
) -> Response:
    stage = await _editable_stage(db, current, stage_id)
    used = await db.scalar(
        select(func.count()).select_from(EstimateItem).where(EstimateItem.stage_id == stage.id)
    )
    if used:
        raise Problem(
            409,
            "stage-in-use",
            "В сметах есть позиции этого этапа",
            "Сначала перенесите или удалите эти позиции.",
        )
    await db.delete(stage)
    audit(
        db,
        entity_type="stage",
        entity_id=stage.id,
        action="stage.deleted",
        actor_id=current.id,
        ip=client_ip(request),
        old={"title": stage.title},
    )
    await db.commit()
    return Response(status_code=204)
