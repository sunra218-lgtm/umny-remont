"""Шаблоны чек-листов: какие ракурсы прораб снимает на этапе (ТЗ 2.3, разделы 4 и 6.2)."""

import uuid

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, select

from app.core.audit import audit
from app.core.deps import Auth, DbSession, PlatformAdmin, client_ip
from app.core.problems import Problem
from app.domain.stage_types import STAGE_TYPES
from app.models import ChecklistItem, ChecklistTemplate, MediaReport

router = APIRouter(tags=["checklists"])


class ItemIn(BaseModel):
    id: uuid.UUID | None = None
    title: str = Field(min_length=2, max_length=300)
    required_angle: str | None = Field(default=None, max_length=200)
    photo_required: bool = True


class TemplateIn(BaseModel):
    stage_type: str
    title: str = Field(min_length=2, max_length=200)
    items: list[ItemIn] = Field(min_length=1, max_length=50)

    @field_validator("stage_type")
    @classmethod
    def known_type(cls, v: str) -> str:
        if v not in STAGE_TYPES:
            raise ValueError("Неизвестный тип этапа")
        return v


class ItemOut(BaseModel):
    id: uuid.UUID
    seq: int
    title: str
    required_angle: str | None
    photo_required: bool


class TemplateOut(BaseModel):
    id: uuid.UUID
    stage_type: str
    stage_type_title: str
    title: str
    items: list[ItemOut]


def template_out(t: ChecklistTemplate, items: list[ChecklistItem]) -> TemplateOut:
    return TemplateOut(
        id=t.id,
        stage_type=t.stage_type,
        stage_type_title=STAGE_TYPES.get(t.stage_type, t.stage_type),
        title=t.title,
        items=[
            ItemOut(
                id=i.id,
                seq=i.seq,
                title=i.title,
                required_angle=i.required_angle,
                photo_required=i.photo_required,
            )
            for i in sorted(items, key=lambda i: i.seq)
        ],
    )


async def items_of(db: DbSession, template_id: uuid.UUID) -> list[ChecklistItem]:
    rows = await db.execute(
        select(ChecklistItem)
        .where(ChecklistItem.template_id == template_id)
        .order_by(ChecklistItem.seq)
    )
    return list(rows.scalars())


async def template_for_stage_type(
    db: DbSession, stage_type: str | None
) -> ChecklistTemplate | None:
    if not stage_type:
        return None
    return (
        await db.execute(
            select(ChecklistTemplate)
            .where(ChecklistTemplate.stage_type == stage_type)
            .order_by(ChecklistTemplate.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


@router.get("/stage-types", summary="Типы этапов")
async def stage_types(current: Auth) -> list[dict[str, str]]:
    return [{"code": k, "title": v} for k, v in STAGE_TYPES.items()]


@router.get("/checklist-templates", response_model=list[TemplateOut], summary="Шаблоны чек-листов")
async def list_templates(current: Auth, db: DbSession) -> list[TemplateOut]:
    templates = list(
        (
            await db.execute(select(ChecklistTemplate).order_by(ChecklistTemplate.stage_type))
        ).scalars()
    )
    items = list(
        (
            await db.execute(
                select(ChecklistItem).where(
                    ChecklistItem.template_id.in_([t.id for t in templates])
                )
            )
        ).scalars()
    )
    return [template_out(t, [i for i in items if i.template_id == t.id]) for t in templates]


@router.post(
    "/checklist-templates",
    response_model=TemplateOut,
    status_code=201,
    summary="Создать шаблон чек-листа",
)
async def create_template(
    body: TemplateIn, current: PlatformAdmin, request: Request, db: DbSession
) -> TemplateOut:
    exists = await template_for_stage_type(db, body.stage_type)
    if exists:
        raise Problem(
            409,
            "template-exists",
            "Для этого типа этапа чек-лист уже есть",
            "Измените существующий.",
        )
    t = ChecklistTemplate(stage_type=body.stage_type, title=body.title)
    db.add(t)
    await db.flush()
    for seq, item in enumerate(body.items, start=1):
        db.add(
            ChecklistItem(
                template_id=t.id,
                seq=seq,
                title=item.title,
                required_angle=item.required_angle,
                photo_required=item.photo_required,
            )
        )
    audit(
        db,
        entity_type="checklist_template",
        entity_id=t.id,
        action="checklist.created",
        actor_id=current.id,
        ip=client_ip(request),
        new=body.model_dump(mode="json"),
    )
    await db.commit()
    return template_out(t, await items_of(db, t.id))


@router.put(
    "/checklist-templates/{template_id}",
    response_model=TemplateOut,
    summary="Изменить шаблон чек-листа",
)
async def update_template(
    template_id: uuid.UUID,
    body: TemplateIn,
    current: PlatformAdmin,
    request: Request,
    db: DbSession,
) -> TemplateOut:
    t = await db.get(ChecklistTemplate, template_id, with_for_update=True)
    if t is None:
        raise Problem(404, "not-found", "Шаблон не найден")
    old_items = {i.id: i for i in await items_of(db, t.id)}
    keep = {i.id for i in body.items if i.id}
    if keep - old_items.keys():
        raise Problem(422, "item-not-found", "Пункт не из этого шаблона")
    removed = set(old_items) - keep
    if removed:
        used = await db.scalar(
            select(func.count())
            .select_from(MediaReport)
            .where(MediaReport.checklist_item_id.in_(removed))
        )
        if used:
            raise Problem(
                409,
                "item-in-use",
                "Пункт уже есть в фотоотчётах, удалить его нельзя",
                "Переименуйте его или снимите отметку «нужно фото».",
            )
        await db.execute(delete(ChecklistItem).where(ChecklistItem.id.in_(removed)))
    old = template_out(t, list(old_items.values())).model_dump(mode="json")
    t.stage_type, t.title = body.stage_type, body.title
    for seq, item in enumerate(body.items, start=1):
        row = old_items.get(item.id) if item.id else None
        if row is None:
            row = ChecklistItem(template_id=t.id)
            db.add(row)
        row.seq, row.title = seq, item.title
        row.required_angle, row.photo_required = item.required_angle, item.photo_required
    audit(
        db,
        entity_type="checklist_template",
        entity_id=t.id,
        action="checklist.updated",
        actor_id=current.id,
        ip=client_ip(request),
        old=old,
        new=body.model_dump(mode="json"),
    )
    await db.commit()
    return template_out(t, await items_of(db, t.id))


@router.delete(
    "/checklist-templates/{template_id}", status_code=204, summary="Удалить шаблон чек-листа"
)
async def delete_template(
    template_id: uuid.UUID, current: PlatformAdmin, request: Request, db: DbSession
) -> Response:
    t = await db.get(ChecklistTemplate, template_id)
    if t is None:
        raise Problem(404, "not-found", "Шаблон не найден")
    items = await items_of(db, t.id)
    used = await db.scalar(
        select(func.count())
        .select_from(MediaReport)
        .where(MediaReport.checklist_item_id.in_([i.id for i in items]))
    )
    if used:
        raise Problem(409, "template-in-use", "По этому чек-листу уже есть фотоотчёты")
    audit(
        db,
        entity_type="checklist_template",
        entity_id=t.id,
        action="checklist.deleted",
        actor_id=current.id,
        ip=client_ip(request),
    )
    await db.delete(t)
    await db.commit()
    return Response(status_code=204)
