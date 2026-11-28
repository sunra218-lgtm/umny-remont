"""Куратор проекта: следит за ходом ремонта.

ИИ-куратор — проверки по данным платформы раз в час: нет фотоотчётов, этап вышел за срок,
приёмка затянулась, замечания технадзора, спор без движения, дизайнер молчит. Сигнал
закрывается сам, когда причина ушла. Куратор-человек (сотрудник платформы или дизайнер)
получает эти сигналы по СМС, закрывает их и пишет заметки заказчику.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import ObjectAccess, load_object
from app.core.audit import audit
from app.core.deps import Auth, CurrentUser, DbSession, client_ip
from app.core.ids import uuid7
from app.core.problems import Problem
from app.core.security import utcnow
from app.models import (
    CuratorSignal,
    DesignerContract,
    DesignerProfile,
    Dispute,
    Inspection,
    MediaReport,
    ObjectAssignment,
    Organization,
    OrgMember,
    RepairObject,
    Stage,
    User,
)
from app.models.enums import (
    AssignmentRole,
    DisputeStatus,
    InspectionResult,
    ObjectStatus,
    OrgRole,
    OrgType,
    StageStatus,
)
from app.modules.notifications import service as notes
from app.modules.objects.router import ensure_assignment

router = APIRouter(tags=["curator"])

NO_PHOTOS_DAYS = 2
ACCEPTANCE_DAYS = 3
DISPUTE_DAYS = 3
DESIGNER_SILENT_DAYS = 2
DESIGN_REVIEW_DAYS = 3
SEVERITY_ORDER = {"alert": 0, "warn": 1, "info": 2}


# ---------- проверки ----------


@dataclass(frozen=True)
class Finding:
    code: str
    ref: str
    severity: str
    text: str


def _d(x: datetime | None) -> str:
    return x.strftime("%d.%m") if x else ""


async def findings(db: AsyncSession, obj: RepairObject, now: datetime) -> list[Finding]:
    out: list[Finding] = []
    today = now.date()
    if obj.status == ObjectStatus.active:
        last = await db.scalar(
            select(func.max(MediaReport.received_server_at)).where(MediaReport.object_id == obj.id)
        )
        since = last or obj.updated_at
        days = (now - since).days
        if days >= NO_PHOTOS_DAYS:
            out.append(
                Finding(
                    "no_photos",
                    "",
                    "warn",
                    f"Нет фотоотчётов {days} дн. Попросите прораба прислать фото.",
                )
            )
        stages = list(
            (
                await db.execute(
                    select(Stage).where(Stage.object_id == obj.id, Stage.deleted_at.is_(None))
                )
            ).scalars()
        )
        for st in stages:
            if (
                st.status in (StageStatus.ready, StageStatus.in_progress, StageStatus.inspection)
                and st.planned_end
                and st.planned_end < today
            ):
                late = (today - st.planned_end).days
                out.append(
                    Finding(
                        "stage_overdue",
                        str(st.id),
                        "alert" if late > 7 else "warn",
                        f"Этап «{st.title}» опаздывает на {late} дн. "
                        f"(план — до {st.planned_end:%d.%m}).",
                    )
                )
            if (
                st.status == StageStatus.acceptance
                and (now - st.updated_at).days >= ACCEPTANCE_DAYS
            ):
                out.append(
                    Finding(
                        "acceptance_waiting",
                        str(st.id),
                        "info",
                        f"Этап «{st.title}» ждёт приёмки с {_d(st.updated_at)}.",
                    )
                )
        open_stage_ids = [
            s.id for s in stages if s.status in (StageStatus.in_progress, StageStatus.inspection)
        ]
        if open_stage_ids:
            insp = (
                await db.execute(
                    select(Inspection)
                    .where(Inspection.stage_id.in_(open_stage_ids), Inspection.result.is_not(None))
                    .order_by(Inspection.stage_id, Inspection.updated_at.desc())
                    .distinct(Inspection.stage_id)
                )
            ).scalars()
            titles = {s.id: s.title for s in stages}
            for i in insp:
                if i.result in (InspectionResult.failed, InspectionResult.remarks):
                    failed = i.result == InspectionResult.failed
                    out.append(
                        Finding(
                            "inspection_remarks",
                            str(i.id),
                            "alert" if failed else "warn",
                            f"Технадзор {'не принял' if failed else 'дал замечания по'} "
                            f"этап{'' if failed else 'у'} «{titles[i.stage_id]}». "
                            "Проверьте, что их исправляют.",
                        )
                    )
    disputes = (
        await db.execute(
            select(Dispute).where(
                Dispute.object_id == obj.id,
                Dispute.status.in_([DisputeStatus.open, DisputeStatus.in_review]),
                Dispute.created_at < now - timedelta(days=DISPUTE_DAYS),
            )
        )
    ).scalars()
    for d in disputes:
        out.append(
            Finding(
                "dispute_open",
                str(d.id),
                "warn",
                f"Спор открыт с {_d(d.created_at)} и ещё не решён.",
            )
        )
    if obj.soul:
        for c in (
            await db.execute(select(DesignerContract).where(DesignerContract.object_id == obj.id))
        ).scalars():
            if c.status == "invited" and (now - c.created_at).days >= DESIGNER_SILENT_DAYS:
                out.append(
                    Finding(
                        "designer_silent",
                        str(c.id),
                        "info",
                        "Дизайнер не ответил на приглашение. Можно выбрать другого.",
                    )
                )
            if (
                c.status == "submitted"
                and c.submitted_at
                and (now - c.submitted_at).days >= DESIGN_REVIEW_DAYS
            ):
                out.append(
                    Finding(
                        "design_review",
                        str(c.id),
                        "info",
                        "Дизайнер сдал проект интерьера, он ждёт вашего решения.",
                    )
                )
    return out


async def check_object(db: AsyncSession, obj: RepairObject, now: datetime | None = None) -> int:
    """Сверяет сигналы объекта с проверками. Возвращает число новых сигналов."""
    now = now or utcnow()
    found = {(f.code, f.ref): f for f in await findings(db, obj, now)}
    open_ = list(
        (
            await db.execute(
                select(CuratorSignal).where(
                    CuratorSignal.object_id == obj.id,
                    CuratorSignal.resolved_at.is_(None),
                    CuratorSignal.author_id.is_(None),
                )
            )
        ).scalars()
    )
    have = {(s.code, s.ref): s for s in open_}
    for key, s in have.items():
        if key not in found:
            s.resolved_at = now
        else:
            f = found[key]
            s.text, s.severity = f.text, f.severity
    new = [f for key, f in found.items() if key not in have]
    for f in new:
        db.add(
            CuratorSignal(
                id=uuid7(),
                object_id=obj.id,
                code=f.code,
                ref=f.ref,
                severity=f.severity,
                text=f.text,
            )
        )
        to: list[uuid.UUID | None] = []
        if f.severity in ("warn", "alert") and obj.curator_kind == "human":
            to.append(obj.curator_id)
        if f.severity == "alert":
            to.append(obj.customer_id)
        if to:
            notes.notify(
                db,
                to,
                "curator_alert",
                text=f.text[:120],
                address=obj.address_text,
                url=notes.url(f"/objects/{obj.id}#curator"),
            )
    return len(new)


async def run_checks(db: AsyncSession, now: datetime | None = None) -> int:
    """Проверка всех объектов в работе (и «с душой» на этапе дизайна)."""
    now = now or utcnow()
    objs = (
        await db.execute(
            select(RepairObject).where(
                RepairObject.deleted_at.is_(None),
                RepairObject.status.not_in([ObjectStatus.completed, ObjectStatus.cancelled]),
            )
        )
    ).scalars()
    n = 0
    for obj in list(objs):
        n += await check_object(db, obj, now)
        await db.commit()
    return n


# ---------- API ----------


class Person(BaseModel):
    id: uuid.UUID
    name: str | None
    phone: str


class SignalOut(BaseModel):
    id: uuid.UUID
    code: str
    severity: str
    text: str
    by_human: bool
    created_at: datetime
    resolved_at: datetime | None


class CuratorOut(BaseModel):
    kind: str
    curator: Person | None
    is_curator: bool
    can_choose: bool  # заказчик выбирает ИИ или человека
    can_assign: bool  # администратор назначает человека
    signals: list[SignalOut]
    notes: list[SignalOut]


def _is_curator(access: ObjectAccess, current: CurrentUser) -> bool:
    return access.obj.curator_kind == "human" and access.obj.curator_id == current.id


async def _out(db: AsyncSession, access: ObjectAccess, current: CurrentUser) -> CuratorOut:
    obj = access.obj
    u = await db.get(User, obj.curator_id) if obj.curator_id else None
    rows = list(
        (
            await db.execute(
                select(CuratorSignal)
                .where(CuratorSignal.object_id == obj.id)
                .order_by(CuratorSignal.created_at.desc())
                .limit(100)
            )
        ).scalars()
    )

    def out(s: CuratorSignal) -> SignalOut:
        return SignalOut(
            id=s.id,
            code=s.code,
            severity=s.severity,
            text=s.text,
            by_human=s.author_id is not None,
            created_at=s.created_at,
            resolved_at=s.resolved_at,
        )

    signals = [s for s in rows if s.code != "note"]
    signals.sort(key=lambda s: (s.resolved_at is not None, SEVERITY_ORDER.get(s.severity, 3)))
    return CuratorOut(
        kind=obj.curator_kind,
        curator=Person(id=u.id, name=u.full_name, phone=u.phone)
        if u and obj.curator_kind == "human"
        else None,
        is_curator=_is_curator(access, current),
        can_choose=access.is_customer or access.is_admin,
        can_assign=access.is_admin,
        signals=[out(s) for s in signals if s.resolved_at is None]
        + [out(s) for s in signals if s.resolved_at is not None][:10],
        notes=[out(s) for s in rows if s.code == "note"][:30],
    )


@router.get("/objects/{object_id}/curator", response_model=CuratorOut, summary="Куратор")
async def get_curator(object_id: uuid.UUID, current: Auth, db: DbSession) -> CuratorOut:
    access = await load_object(db, current, object_id)
    return await _out(db, access, current)


async def _can_be_curator(db: AsyncSession, user_id: uuid.UUID) -> bool:
    staff = await db.scalar(
        select(OrgMember.id)
        .join(Organization, Organization.id == OrgMember.org_id)
        .where(Organization.type == OrgType.platform, OrgMember.user_id == user_id)
        .limit(1)
    )
    if staff:
        return True
    return bool(
        await db.scalar(
            select(DesignerProfile.id).where(
                DesignerProfile.user_id == user_id, DesignerProfile.verified_at.is_not(None)
            )
        )
    )


class CuratorIn(BaseModel):
    kind: str = Field(pattern="^(ai|human)$")
    user_id: uuid.UUID | None = None


@router.put("/objects/{object_id}/curator", response_model=CuratorOut, summary="Выбрать куратора")
async def set_curator(
    object_id: uuid.UUID, body: CuratorIn, current: Auth, request: Request, db: DbSession
) -> CuratorOut:
    access = await load_object(db, current, object_id, lock=True)
    if not (access.is_customer or access.is_admin):
        raise Problem(403, "forbidden", "Куратора выбирает заказчик")
    obj = access.obj
    if body.user_id is not None:
        if not access.is_admin:
            raise Problem(403, "forbidden", "Куратора-человека назначает администратор")
        if body.kind != "human":
            raise Problem(422, "kind-mismatch", "Человека назначают куратором вида «человек»")
        if not await _can_be_curator(db, body.user_id):
            raise Problem(
                422, "not-curator", "Куратор — сотрудник платформы или проверенный дизайнер"
            )
    old = {"kind": obj.curator_kind, "user_id": obj.curator_id}
    new_user = body.user_id if body.kind == "human" else None
    if body.kind == "human" and body.user_id is None:
        new_user = obj.curator_id  # заказчик просит человека — администратор назначит
    if obj.curator_id and obj.curator_id != new_user:
        row = (
            await db.execute(
                select(ObjectAssignment).where(
                    ObjectAssignment.object_id == obj.id,
                    ObjectAssignment.user_id == obj.curator_id,
                    ObjectAssignment.role == AssignmentRole.curator,
                )
            )
        ).scalar_one_or_none()
        if row is not None:
            await db.delete(row)
    obj.curator_kind, obj.curator_id = body.kind, new_user
    if new_user and new_user != old["user_id"]:
        await ensure_assignment(db, obj.id, new_user, AssignmentRole.curator)
        notes.notify(
            db,
            [new_user],
            "curator_assigned",
            address=obj.address_text,
            url=notes.url(f"/objects/{obj.id}#curator"),
        )
    if body.kind == "human" and new_user is None and old["kind"] != "human":
        notes.notify(
            db,
            await notes.platform_staff_ids(db, [OrgRole.platform_admin]),
            "curator_alert",
            text="Заказчик просит куратора-человека, назначьте",
            address=obj.address_text,
            url=notes.url(f"/objects/{obj.id}#curator"),
        )
    audit(
        db,
        entity_type="object",
        entity_id=obj.id,
        action="object.curator_set",
        actor_id=current.id,
        ip=client_ip(request),
        old=old,
        new={"kind": body.kind, "user_id": new_user},
    )
    await db.commit()
    access = await load_object(db, current, object_id)
    return await _out(db, access, current)


class NoteIn(BaseModel):
    text: str = Field(min_length=2, max_length=1000)


@router.post(
    "/objects/{object_id}/curator/notes",
    response_model=CuratorOut,
    status_code=201,
    summary="Заметка куратора",
)
async def add_note(
    object_id: uuid.UUID, body: NoteIn, current: Auth, request: Request, db: DbSession
) -> CuratorOut:
    access = await load_object(db, current, object_id)
    if not (_is_curator(access, current) or access.is_admin):
        raise Problem(403, "forbidden", "Заметки пишет куратор")
    obj = access.obj
    s = CuratorSignal(
        id=uuid7(),
        object_id=obj.id,
        code="note",
        ref=str(uuid7()),
        severity="info",
        text=body.text.strip(),
        author_id=current.id,
    )
    db.add(s)
    audit(
        db,
        entity_type="object",
        entity_id=obj.id,
        action="curator.note",
        actor_id=current.id,
        ip=client_ip(request),
    )
    notes.notify(db, [obj.customer_id], "curator_note", url=notes.url(f"/objects/{obj.id}#curator"))
    await db.commit()
    return await _out(db, access, current)


@router.post(
    "/curator-signals/{signal_id}/resolve", response_model=CuratorOut, summary="Закрыть сигнал"
)
async def resolve(
    signal_id: uuid.UUID, current: Auth, request: Request, db: DbSession
) -> CuratorOut:
    s = await db.get(CuratorSignal, signal_id)
    if s is None:
        raise Problem(404, "not-found", "Сигнал не найден")
    access = await load_object(db, current, s.object_id)
    if not (_is_curator(access, current) or access.is_admin):
        raise Problem(403, "forbidden", "Сигналы закрывает куратор")
    if s.resolved_at is None:
        s.resolved_at = utcnow()
        audit(
            db,
            entity_type="object",
            entity_id=s.object_id,
            action="curator.resolved",
            actor_id=current.id,
            ip=client_ip(request),
            new={"code": s.code},
        )
        await db.commit()
    return await _out(db, access, current)


@router.post(
    "/objects/{object_id}/curator/check", response_model=CuratorOut, summary="Проверить сейчас"
)
async def check_now(object_id: uuid.UUID, current: Auth, db: DbSession) -> CuratorOut:
    access = await load_object(db, current, object_id)
    if not (access.is_customer or access.is_admin or _is_curator(access, current)):
        raise Problem(403, "forbidden", "Нет доступа")
    await check_object(db, access.obj)
    await db.commit()
    return await _out(db, access, current)


class CuratedObject(BaseModel):
    id: uuid.UUID
    address: str
    status: str
    open_signals: int
    worst: str | None


@router.get("/curator/objects", response_model=list[CuratedObject], summary="Мои объекты")
async def my_objects(current: Auth, db: DbSession) -> list[CuratedObject]:
    objs = list(
        (
            await db.execute(
                select(RepairObject)
                .where(
                    RepairObject.curator_kind == "human",
                    RepairObject.curator_id == current.id,
                    RepairObject.deleted_at.is_(None),
                )
                .order_by(RepairObject.updated_at.desc())
            )
        ).scalars()
    )
    out = []
    for o in objs:
        sev = list(
            (
                await db.execute(
                    select(CuratorSignal.severity).where(
                        CuratorSignal.object_id == o.id,
                        CuratorSignal.resolved_at.is_(None),
                        CuratorSignal.code != "note",
                    )
                )
            ).scalars()
        )
        out.append(
            CuratedObject(
                id=o.id,
                address=o.address_text,
                status=o.status.value,
                open_signals=len(sev),
                worst=min(sev, key=lambda x: SEVERITY_ORDER.get(x, 3)) if sev else None,
            )
        )
    out.sort(key=lambda c: SEVERITY_ORDER.get(c.worst or "", 3))
    return out


@router.get("/curator/candidates", response_model=list[Person], summary="Кого назначить куратором")
async def candidates(current: Auth, db: DbSession) -> list[Person]:
    if not current.is_platform_admin:
        raise Problem(403, "forbidden", "Только администратор")
    staff = (
        select(OrgMember.user_id)
        .join(Organization, Organization.id == OrgMember.org_id)
        .where(Organization.type == OrgType.platform)
    )
    designers = select(DesignerProfile.user_id).where(DesignerProfile.verified_at.is_not(None))
    users = (
        await db.execute(
            select(User).where(User.id.in_(staff) | User.id.in_(designers)).order_by(User.full_name)
        )
    ).scalars()
    return [Person(id=u.id, name=u.full_name, phone=u.phone) for u in users]
