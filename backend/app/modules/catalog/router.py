"""Справочник расценок: шаблоны работ и материалов для смет."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from app.core.audit import audit
from app.core.deps import Auth, DbSession, PlatformAdmin, client_ip
from app.core.problems import Problem
from app.models import PriceItem
from app.models.enums import EstimateItemCategory

router = APIRouter(prefix="/price-items", tags=["catalog"])


class PriceItemIn(BaseModel):
    category: EstimateItemCategory
    stage_type: str | None = Field(default=None, max_length=50)
    title: str = Field(min_length=2, max_length=500)
    unit: str = Field(min_length=1, max_length=20)
    unit_price_kop: int = Field(ge=0, le=10_000_000_000)
    sku: str | None = Field(default=None, max_length=100)


class PriceItemUpdate(BaseModel):
    category: EstimateItemCategory | None = None
    stage_type: str | None = Field(default=None, max_length=50)
    title: str | None = Field(default=None, min_length=2, max_length=500)
    unit: str | None = Field(default=None, min_length=1, max_length=20)
    unit_price_kop: int | None = Field(default=None, ge=0, le=10_000_000_000)
    sku: str | None = Field(default=None, max_length=100)
    is_active: bool | None = None


class PriceItemOut(PriceItemIn):
    id: uuid.UUID
    is_active: bool
    updated_at: datetime


@router.get("", response_model=list[PriceItemOut], summary="Справочник расценок")
async def list_items(
    current: Auth,
    db: DbSession,
    q: str | None = Query(default=None, max_length=100),
    stage_type: str | None = None,
    category: EstimateItemCategory | None = None,
    include_inactive: bool = False,
) -> list[PriceItemOut]:
    if not current.memberships:
        raise Problem(403, "forbidden", "Справочник доступен сотрудникам организаций")
    stmt = select(PriceItem)
    if not include_inactive:
        stmt = stmt.where(PriceItem.is_active.is_(True))
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(or_(PriceItem.title.ilike(pattern), PriceItem.sku.ilike(pattern)))
    if stage_type:
        stmt = stmt.where(PriceItem.stage_type == stage_type)
    if category:
        stmt = stmt.where(PriceItem.category == category)
    rows = await db.execute(
        stmt.order_by(PriceItem.stage_type, PriceItem.category, PriceItem.title).limit(1000)
    )
    return [PriceItemOut.model_validate(r, from_attributes=True) for r in rows.scalars()]


@router.post("", response_model=PriceItemOut, status_code=201, summary="Добавить расценку")
async def create_item(
    body: PriceItemIn, current: PlatformAdmin, request: Request, db: DbSession
) -> PriceItemOut:
    item = PriceItem(**body.model_dump())
    db.add(item)
    await db.flush()
    audit(
        db,
        entity_type="price_item",
        entity_id=item.id,
        action="price_item.created",
        actor_id=current.id,
        ip=client_ip(request),
        new=body.model_dump(),
    )
    await db.commit()
    await db.refresh(item)
    return PriceItemOut.model_validate(item, from_attributes=True)


@router.patch("/{item_id}", response_model=PriceItemOut, summary="Изменить расценку")
async def update_item(
    item_id: uuid.UUID,
    body: PriceItemUpdate,
    current: PlatformAdmin,
    request: Request,
    db: DbSession,
) -> PriceItemOut:
    item = await db.get(PriceItem, item_id)
    if item is None:
        raise Problem(404, "not-found", "Расценка не найдена")
    changes = body.model_dump(exclude_unset=True)
    old = {k: getattr(item, k) for k in changes}
    for key, value in changes.items():
        setattr(item, key, value)
    if changes:
        audit(
            db,
            entity_type="price_item",
            entity_id=item.id,
            action="price_item.updated",
            actor_id=current.id,
            ip=client_ip(request),
            old=old,
            new=changes,
        )
        await db.commit()
        await db.refresh(item)
    return PriceItemOut.model_validate(item, from_attributes=True)
