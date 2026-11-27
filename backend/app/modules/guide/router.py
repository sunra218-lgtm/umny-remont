"""Подсказки мастерам: фото правильного результата, калькулятор материалов,
«спросить инженера» с фото и помощник по техкартам."""

import asyncio
import uuid
from datetime import datetime

from fastapi import APIRouter, File, Form, Query, Request, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.access import ObjectAccess, load_object
from app.core.audit import audit
from app.core.config import get_settings
from app.core.deps import Auth, CurrentUser, DbSession, client_ip
from app.core.ids import uuid7
from app.core.problems import Problem
from app.core.security import utcnow
from app.core.storage import get_storage, signed_file_url
from app.models import EngineerQuestion, ObjectAssignment, RepairObject, Stage, TechCard, User
from app.models.enums import AssignmentRole, OrgRole
from app.modules.field.antifraud import NotAnImage, inspect_image
from app.modules.guide import assistant
from app.modules.guide.calc import CalcRow, calculate
from app.modules.notifications import service as notes
from app.modules.techcards.router import CardOut, card_out
from app.modules.techcards.service import card_for

router = APIRouter(tags=["guide"])


async def _image(upload: UploadFile) -> tuple[bytes, str, str]:
    s = get_settings()
    data = await upload.read(s.max_photo_bytes + 1)
    if len(data) > s.max_photo_bytes:
        raise Problem(413, "file-too-large", "Фото слишком большое")
    try:
        info = await asyncio.to_thread(inspect_image, data)
    except NotAnImage as exc:
        raise Problem(422, "not-an-image", str(exc)) from None
    return data, info.content_type, info.ext


# ---------- фото правильного результата ----------


@router.put(
    "/tech-cards/{stage_type}/steps/{key}/example",
    response_model=CardOut,
    summary="Фото правильного результата шага",
)
async def put_example(
    stage_type: str,
    key: str,
    current: Auth,
    request: Request,
    db: DbSession,
    photo: UploadFile = File(...),
) -> CardOut:
    if not current.has_platform_role(OrgRole.platform_admin, OrgRole.inspector):
        raise Problem(403, "forbidden", "Фото-образцы загружают администратор и инженер")
    card = await card_for(db, stage_type)
    if card is None:
        raise Problem(404, "not-found", "Техкарты нет")
    await db.refresh(card, with_for_update=True)
    if key not in {s["key"] for s in card.steps}:
        raise Problem(404, "not-found", "Шага нет в техкарте")
    data, content_type, ext = await _image(photo)
    storage_key = f"examples/{stage_type}/{key}-{uuid7()}.{ext}"
    await get_storage().put(storage_key, data, content_type)
    # Образец не меняет нормы — отметку «проверено инженером» не снимаем.
    card.steps = [{**s, "example_key": storage_key} if s["key"] == key else s for s in card.steps]
    audit(
        db,
        entity_type="tech_card",
        entity_id=card.id,
        action="tech_card.example_uploaded",
        actor_id=current.id,
        ip=client_ip(request),
        new={"step": key, "key": storage_key},
    )
    await db.commit()
    await db.refresh(card)
    return card_out(card)


@router.get(
    "/tech-cards/{stage_type}/steps/{key}/example",
    summary="Фото-образец шага (общедоступно: в нём нет личных данных)",
    response_class=Response,
)
async def get_example(stage_type: str, key: str, db: DbSession) -> Response:
    card = await card_for(db, stage_type)
    step = next((s for s in (card.steps if card else []) if s["key"] == key), None)
    if step is None or not step.get("example_key"):
        raise Problem(404, "not-found", "Образца нет")
    ext = step["example_key"].rsplit(".", 1)[-1]
    content_type = {"png": "image/png", "webp": "image/webp"}.get(ext, "image/jpeg")
    return Response(
        await get_storage().get(step["example_key"]),
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=3600", "X-Content-Type-Options": "nosniff"},
    )


# ---------- калькулятор ----------


class CalcIn(BaseModel):
    area_m2: float | None = Field(default=None, gt=0, le=100_000)
    length_m: float | None = Field(default=None, gt=0, le=100_000)
    count: float | None = Field(default=None, gt=0, le=100_000)
    volume_m3: float | None = Field(default=None, gt=0, le=10_000)
    thickness: dict[str, float] = Field(default_factory=dict)


@router.post(
    "/tech-cards/{stage_type}/calc",
    response_model=list[CalcRow],
    summary="Калькулятор расхода материалов",
)
async def calc(stage_type: str, body: CalcIn, current: Auth, db: DbSession) -> list[CalcRow]:
    card = await card_for(db, stage_type)
    if card is None:
        raise Problem(404, "not-found", "Техкарты нет")
    if any(not 0 < v <= 500 for v in body.thickness.values()):
        raise Problem(422, "bad-thickness", "Толщина слоя — от 0 до 500 мм")
    amount = {
        "м²": body.area_m2 or 0,
        "м.п.": body.length_m or 0,
        "шт": body.count or 0,
        "м³": body.volume_m3 or 0,
    }
    return calculate(card.materials, amount, body.thickness)


# ---------- помощник ----------


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    stage_type: str | None = None


@router.post("/assistant/ask", response_model=assistant.Answer, summary="Помощник по техкартам")
async def ask(body: AskIn, current: Auth, db: DbSession) -> assistant.Answer:
    cards = [
        {"stage_type": c.stage_type, "title": c.title, "steps": c.steps}
        for c in (await db.execute(select(TechCard))).scalars()
    ]
    return await assistant.ask(cards, body.question, body.stage_type)


# ---------- спросить инженера ----------


class QuestionOut(BaseModel):
    id: uuid.UUID
    object_id: uuid.UUID
    address: str
    stage_id: uuid.UUID | None
    stage_title: str | None
    step_key: str | None
    author_name: str
    text: str
    photo_url: str | None
    status: str
    answer: str | None
    answered_at: datetime | None
    created_at: datetime
    can_answer: bool


def _is_engineer(current: CurrentUser, access: ObjectAccess | None = None) -> bool:
    if current.has_platform_role(OrgRole.platform_admin, OrgRole.inspector):
        return True
    return access is not None and AssignmentRole.inspector in access.assignment_roles


async def _out(
    db: DbSession, q: EngineerQuestion, current: CurrentUser, engineer: bool
) -> QuestionOut:
    obj = await db.get(RepairObject, q.object_id)
    stage = await db.get(Stage, q.stage_id) if q.stage_id else None
    author = await db.get(User, q.author_id)
    return QuestionOut(
        id=q.id,
        object_id=q.object_id,
        address=obj.address_text if obj else "",
        stage_id=q.stage_id,
        stage_title=stage.title if stage else None,
        step_key=q.step_key,
        author_name=(author.full_name or "Мастер") if author else "Мастер",
        text=q.text,
        photo_url=signed_file_url("question", q.id, current.id) if q.photo_key else None,
        status=q.status,
        answer=q.answer,
        answered_at=q.answered_at,
        created_at=q.created_at,
        can_answer=engineer and q.status == "open",
    )


@router.post(
    "/stages/{stage_id}/questions",
    response_model=QuestionOut,
    status_code=201,
    summary="Спросить инженера",
)
async def create_question(
    stage_id: uuid.UUID,
    current: Auth,
    request: Request,
    db: DbSession,
    text: str = Form(..., min_length=3, max_length=2000),
    step_key: str | None = Form(default=None, max_length=50),
    photo: UploadFile | None = File(default=None),
) -> QuestionOut:
    stage = await db.get(Stage, stage_id)
    if stage is None or stage.deleted_at is not None:
        raise Problem(404, "not-found", "Этап не найден")
    access = await load_object(db, current, stage.object_id)
    if not access.is_crew:
        raise Problem(403, "forbidden", "Вопрос задаёт бригада объекта")
    q = EngineerQuestion(
        id=uuid7(),
        object_id=stage.object_id,
        stage_id=stage.id,
        author_id=current.id,
        step_key=step_key or None,
        text=text.strip(),
    )
    if photo is not None and photo.filename:
        data, content_type, ext = await _image(photo)
        q.photo_key = f"questions/{stage.object_id}/{q.id}.{ext}"
        q.photo_type = content_type
        await get_storage().put(q.photo_key, data, content_type)
    db.add(q)
    await db.flush()
    audit(
        db,
        entity_type="engineer_question",
        entity_id=q.id,
        action="question.asked",
        actor_id=current.id,
        ip=client_ip(request),
        new={"stage_id": stage.id, "photo": bool(q.photo_key), "step_key": q.step_key},
    )
    assigned = (
        await db.execute(
            select(ObjectAssignment.user_id).where(
                ObjectAssignment.object_id == stage.object_id,
                ObjectAssignment.role == AssignmentRole.inspector,
            )
        )
    ).scalars()
    # Назначенный на объект инженер, а если его нет — все инженеры платформы.
    to = list(assigned) or await notes.platform_staff_ids(db, [OrgRole.inspector])
    notes.notify(
        db,
        to,
        "question_asked",
        address=access.obj.address_text,
        url=notes.url("/inspections#questions"),
    )
    await db.commit()
    return await _out(db, q, current, False)


@router.get(
    "/objects/{object_id}/questions",
    response_model=list[QuestionOut],
    summary="Вопросы бригады по объекту",
)
async def object_questions(object_id: uuid.UUID, current: Auth, db: DbSession) -> list[QuestionOut]:
    access = await load_object(db, current, object_id)
    engineer = _is_engineer(current, access)
    rows = (
        await db.execute(
            select(EngineerQuestion)
            .where(EngineerQuestion.object_id == object_id)
            .order_by(EngineerQuestion.created_at.desc())
            .limit(100)
        )
    ).scalars()
    return [await _out(db, q, current, engineer) for q in rows]


@router.get("/questions", response_model=list[QuestionOut], summary="Вопросы бригад инженеру")
async def engineer_questions(
    current: Auth, db: DbSession, open_only: bool = Query(default=True)
) -> list[QuestionOut]:
    """Инженер и администратор платформы видят вопросы по всем объектам,
    назначенный на объект инженер — по своим."""
    stmt = select(EngineerQuestion)
    if not _is_engineer(current):
        mine = select(ObjectAssignment.object_id).where(
            ObjectAssignment.user_id == current.id,
            ObjectAssignment.role == AssignmentRole.inspector,
        )
        stmt = stmt.where(EngineerQuestion.object_id.in_(mine))
    if open_only:
        stmt = stmt.where(EngineerQuestion.status == "open")
    stmt = stmt.order_by(EngineerQuestion.created_at.desc()).limit(100)
    rows = (await db.execute(stmt)).scalars()
    return [await _out(db, q, current, True) for q in rows]


class AnswerIn(BaseModel):
    answer: str = Field(min_length=2, max_length=4000)


@router.post("/questions/{question_id}/answer", response_model=QuestionOut, summary="Ответить")
async def answer_question(
    question_id: uuid.UUID, body: AnswerIn, current: Auth, request: Request, db: DbSession
) -> QuestionOut:
    q = await db.get(EngineerQuestion, question_id, with_for_update=True)
    if q is None:
        raise Problem(404, "not-found", "Вопрос не найден")
    # Инженер платформы отвечает по любому объекту, остальные — только по своим.
    access = None if _is_engineer(current) else await load_object(db, current, q.object_id)
    if not _is_engineer(current, access):
        raise Problem(403, "forbidden", "Отвечает инженер технадзора")
    if q.status != "open":
        raise Problem(409, "already-answered", "На вопрос уже ответили")
    q.status, q.answer = "answered", body.answer.strip()
    q.answered_by, q.answered_at = current.id, utcnow()
    audit(
        db,
        entity_type="engineer_question",
        entity_id=q.id,
        action="question.answered",
        actor_id=current.id,
        ip=client_ip(request),
    )
    notes.notify(db, [q.author_id], "question_answered", url=notes.url("/lite"))
    await db.commit()
    return await _out(db, q, current, True)
