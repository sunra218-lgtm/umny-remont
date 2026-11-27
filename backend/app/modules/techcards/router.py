"""Технологические карты: просмотр всем участникам, правка и проверка — платформа."""

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import select

from app.core.access import load_object
from app.core.audit import audit
from app.core.deps import Auth, CurrentUser, DbSession, client_ip
from app.core.problems import Problem
from app.core.security import utcnow
from app.domain.stage_types import STAGE_TYPES
from app.models import Stage, TechCard
from app.models.enums import OrgRole
from app.modules.techcards.service import CardIn, Material, Step, card_for, sync_checklist

router = APIRouter(tags=["tech-cards"])


class CardBrief(BaseModel):
    stage_type: str
    stage_type_title: str
    title: str
    steps: int
    version: int
    verified_at: datetime | None


class CardOut(BaseModel):
    stage_type: str
    stage_type_title: str
    title: str
    summary: str | None
    norms: list[str]
    steps: list[Step]
    materials: list[Material]
    version: int
    verified_at: datetime | None
    updated_at: datetime


def card_out(card: TechCard) -> CardOut:
    return CardOut(
        stage_type=card.stage_type,
        stage_type_title=STAGE_TYPES.get(card.stage_type, card.stage_type),
        title=card.title,
        summary=card.summary,
        norms=card.norms,
        steps=[Step.model_validate(s) for s in card.steps],
        materials=[Material.model_validate(m) for m in card.materials],
        version=card.version,
        verified_at=card.verified_at,
        updated_at=card.updated_at,
    )


def _editor(current: CurrentUser) -> None:
    if not current.has_platform_role(OrgRole.platform_admin, OrgRole.inspector):
        raise Problem(403, "forbidden", "Техкарты правят администратор и инженер технадзора")


async def _card(db: DbSession, stage_type: str, *, lock: bool = False) -> TechCard:
    card = await card_for(db, stage_type)
    if card is None:
        raise Problem(404, "not-found", "Техкарты для этого вида работ нет")
    if lock:
        await db.refresh(card, with_for_update=True)
    return card


@router.get("/tech-cards", response_model=list[CardBrief], summary="Техкарты")
async def list_cards(current: Auth, db: DbSession) -> list[CardBrief]:
    rows = (await db.execute(select(TechCard).order_by(TechCard.stage_type))).scalars()
    order = list(STAGE_TYPES)
    cards = sorted(rows, key=lambda c: order.index(c.stage_type) if c.stage_type in order else 99)
    return [
        CardBrief(
            stage_type=c.stage_type,
            stage_type_title=STAGE_TYPES.get(c.stage_type, c.stage_type),
            title=c.title,
            steps=len(c.steps),
            version=c.version,
            verified_at=c.verified_at,
        )
        for c in cards
    ]


@router.get("/tech-cards/{stage_type}", response_model=CardOut, summary="Техкарта")
async def get_card(stage_type: str, current: Auth, db: DbSession) -> CardOut:
    return card_out(await _card(db, stage_type))


@router.put("/tech-cards/{stage_type}", response_model=CardOut, summary="Изменить техкарту")
async def put_card(
    stage_type: str, body: CardIn, current: Auth, request: Request, db: DbSession
) -> CardOut:
    """Создаёт или заменяет карту целиком. Отметка «проверено инженером» снимается,
    пункты чек-листа ракурсов пересобираются из шагов."""
    _editor(current)
    if stage_type not in STAGE_TYPES:
        raise Problem(404, "not-found", "Неизвестный вид работ")
    card = await card_for(db, stage_type)
    old: dict[str, Any] | None = None
    data = body.model_dump(mode="json")
    # Фото-образцы загружаются отдельно; ссылку на файл из запроса не принимаем.
    examples = {st["key"]: st.get("example_key") for st in (card.steps if card else [])}
    data["steps"] = [{**st, "example_key": examples.get(st["key"])} for st in data["steps"]]
    if card is None:
        card = TechCard(stage_type=stage_type, **data)
        db.add(card)
    else:
        await db.refresh(card, with_for_update=True)
        old = {"version": card.version, "steps": card.steps, "materials": card.materials}
        for key, value in data.items():
            setattr(card, key, value)
        card.version += 1
    card.verified_at, card.verified_by = None, None
    await db.flush()
    await sync_checklist(db, card)
    audit(
        db,
        entity_type="tech_card",
        entity_id=card.id,
        action="tech_card.updated" if old else "tech_card.created",
        actor_id=current.id,
        ip=client_ip(request),
        old=old,
        new=data,
    )
    await db.commit()
    await db.refresh(card)
    return card_out(card)


@router.post(
    "/tech-cards/{stage_type}/verify", response_model=CardOut, summary="Техкарта проверена"
)
async def verify_card(stage_type: str, current: Auth, request: Request, db: DbSession) -> CardOut:
    _editor(current)
    card = await _card(db, stage_type, lock=True)
    card.verified_at, card.verified_by = utcnow(), current.id
    audit(
        db,
        entity_type="tech_card",
        entity_id=card.id,
        action="tech_card.verified",
        actor_id=current.id,
        ip=client_ip(request),
        new={"version": card.version},
    )
    await db.commit()
    await db.refresh(card)
    return card_out(card)


@router.get(
    "/stages/{stage_id}/tech-card",
    response_model=CardOut | None,
    summary="Как должно быть: техкарта этапа",
)
async def stage_card(stage_id: uuid.UUID, current: Auth, db: DbSession) -> CardOut | None:
    stage = await db.get(Stage, stage_id)
    if stage is None or stage.deleted_at is not None:
        raise Problem(404, "not-found", "Этап не найден")
    await load_object(db, current, stage.object_id)
    card = await card_for(db, stage.stage_type)
    return card_out(card) if card else None
