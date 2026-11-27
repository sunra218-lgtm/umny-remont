"""Технологические карты: формат, стартовое наполнение, сборка чек-листа ракурсов из шагов."""

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.stage_types import STAGE_TYPES
from app.domain.tech_cards import TECH_CARDS
from app.models import ChecklistItem, ChecklistTemplate, MediaReport, TechCard

KEY = r"^[a-z0-9_]{1,50}$"


class Step(BaseModel):
    key: str = Field(pattern=KEY)
    title: str = Field(min_length=2, max_length=300)
    how: str = Field(min_length=2, max_length=3000)
    tolerance: str | None = Field(default=None, max_length=1000)
    check: str | None = Field(default=None, max_length=500)
    mistakes: list[str] = Field(default_factory=list, max_length=20)
    photo: str | None = Field(default=None, max_length=200)
    hidden: bool = False
    inspector: bool = False
    # Фото правильного результата (ключ в хранилище), загружает администратор.
    example_key: str | None = Field(default=None, max_length=300)


class Material(BaseModel):
    key: str = Field(pattern=KEY)
    name: str = Field(min_length=2, max_length=200)
    rate: float = Field(gt=0, le=10_000)
    per: Literal["м²", "м.п.", "шт", "м³"]
    unit: str = Field(min_length=1, max_length=20)
    per_mm: bool = False
    thickness_mm: float | None = Field(default=None, gt=0, le=500)
    pack: float | None = Field(default=None, gt=0, le=100_000)
    pack_title: str | None = Field(default=None, max_length=100)
    reserve_pct: int = Field(default=10, ge=0, le=100)
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _thickness(self) -> "Material":
        if self.per_mm and not self.thickness_mm:
            raise ValueError(f"{self.name}: для расхода на 1 мм укажите толщину слоя")
        return self


class CardIn(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    summary: str | None = Field(default=None, max_length=3000)
    norms: list[str] = Field(default_factory=list, max_length=30)
    steps: list[Step] = Field(min_length=1, max_length=40)
    materials: list[Material] = Field(default_factory=list, max_length=40)

    @field_validator("steps")
    @classmethod
    def _unique_steps(cls, v: list[Step]) -> list[Step]:
        keys = [s.key for s in v]
        if len(keys) != len(set(keys)):
            raise ValueError("Коды шагов должны быть разными")
        return v

    @field_validator("materials")
    @classmethod
    def _unique_materials(cls, v: list[Material]) -> list[Material]:
        keys = [m.key for m in v]
        if len(keys) != len(set(keys)):
            raise ValueError("Коды материалов должны быть разными")
        return v


async def card_for(db: AsyncSession, stage_type: str | None) -> TechCard | None:
    if not stage_type:
        return None
    return (
        await db.execute(select(TechCard).where(TechCard.stage_type == stage_type))
    ).scalar_one_or_none()


async def sync_checklist(db: AsyncSession, card: TechCard) -> ChecklistTemplate:
    """Пункты чек-листа ракурсов = шаги техкарты, где нужно фото.

    Пункт, по которому уже есть фото, не удаляется (на него ссылаются отчёты): он остаётся
    без отметки «нужно фото». Совпадение — по коду шага, для старых пунктов — по названию.
    """
    template = (
        await db.execute(
            select(ChecklistTemplate)
            .where(ChecklistTemplate.stage_type == card.stage_type)
            .order_by(ChecklistTemplate.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if template is None:
        template = ChecklistTemplate(stage_type=card.stage_type, title=card.title)
        db.add(template)
        await db.flush()
    old = list(
        (
            await db.execute(select(ChecklistItem).where(ChecklistItem.template_id == template.id))
        ).scalars()
    )
    by_key = {i.step_key: i for i in old if i.step_key}
    by_title = {i.title.casefold(): i for i in old if not i.step_key}
    kept: set[Any] = set()
    seq = 0
    for s in card.steps:
        if not s.get("photo"):
            continue
        seq += 1
        item = by_key.get(s["key"]) or by_title.pop(s["title"].casefold(), None)
        if item is None or item.id in kept:
            item = ChecklistItem(template_id=template.id)
            db.add(item)
        item.seq, item.title, item.step_key = seq, s["title"][:300], s["key"]
        item.required_angle, item.photo_required = (s["photo"] or "")[:200], True
        await db.flush()
        kept.add(item.id)
    leftovers = [i for i in old if i.id not in kept]
    if leftovers:
        used = set(
            (
                await db.execute(
                    select(MediaReport.checklist_item_id)
                    .where(MediaReport.checklist_item_id.in_([i.id for i in leftovers]))
                    .distinct()
                )
            ).scalars()
        )
        for item in leftovers:
            if item.id in used:
                seq += 1
                item.seq, item.photo_required = seq, False
        unused = [i.id for i in leftovers if i.id not in used]
        if unused:
            await db.execute(delete(ChecklistItem).where(ChecklistItem.id.in_(unused)))
    template.title = card.title
    return template


async def seed_tech_cards(db: AsyncSession) -> int:
    """Стартовые техкарты: только если таблица пуста. Чек-листы собираются из шагов."""
    if await db.scalar(select(func.count()).select_from(TechCard)):
        return 0
    for raw in TECH_CARDS:
        assert raw["stage_type"] in STAGE_TYPES
        data = CardIn.model_validate(raw)
        card = TechCard(stage_type=raw["stage_type"], **data.model_dump(mode="json"))
        db.add(card)
        await db.flush()
        await sync_checklist(db, card)
    await db.commit()
    return len(TECH_CARDS)
