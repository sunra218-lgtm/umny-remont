import uuid
from datetime import datetime

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.core.audit import audit
from app.core.deps import Auth, CurrentUser, DbSession, PlatformAdmin, client_ip
from app.core.pagination import Page
from app.core.problems import Problem
from app.core.security import utcnow
from app.models import DeviceSession, Organization, OrgMember, User
from app.models.enums import ROLES_BY_ORG_TYPE, OrgRole, OrgType, SessionKind
from app.modules.auth.service import get_or_create_user
from app.modules.orgs.schemas import MemberAdd, MemberOut, OrgCreate, OrgOut, OrgUpdate

router = APIRouter(prefix="/organizations", tags=["organizations"])

# Кто из сотрудников организации может управлять её составом (кроме админа платформы).
ORG_MANAGERS = {
    OrgType.contractor: {OrgRole.contractor_admin},
    OrgType.supplier: {OrgRole.supplier_manager},
    OrgType.platform: set(),
}


def _out(org: Organization) -> OrgOut:
    return OrgOut.model_validate(org, from_attributes=True)


async def _get_org(db: DbSession, org_id: uuid.UUID) -> Organization:
    org = await db.get(Organization, org_id)
    if org is None or org.deleted_at is not None:
        raise Problem(404, "not-found", "Организация не найдена")
    return org


def _can_view(current: CurrentUser, org: Organization) -> bool:
    return current.is_platform_admin or bool(current.roles_in_org(org.id))


def _can_manage_members(current: CurrentUser, org: Organization) -> bool:
    return current.is_platform_admin or bool(current.roles_in_org(org.id) & ORG_MANAGERS[org.type])


@router.post("", response_model=OrgOut, status_code=201, summary="Добавить организацию")
async def create_org(
    body: OrgCreate, current: PlatformAdmin, request: Request, db: DbSession
) -> OrgOut:
    org = Organization(**body.model_dump())
    db.add(org)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise Problem(409, "inn-taken", "Организация с таким ИНН уже есть") from None
    audit(
        db,
        entity_type="organization",
        entity_id=org.id,
        action="organization.created",
        actor_id=current.id,
        ip=client_ip(request),
        new=body.model_dump(),
    )
    await db.commit()
    await db.refresh(org)
    return _out(org)


@router.get("", response_model=Page[OrgOut], summary="Список организаций")
async def list_orgs(
    current: Auth,
    db: DbSession,
    type: OrgType | None = None,
    q: str | None = Query(default=None, max_length=100),
    cursor: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> Page[OrgOut]:
    stmt = select(Organization).where(Organization.deleted_at.is_(None))
    # Менеджер закупок выбирает поставщика для корзины — ему виден список поставщиков.
    buyer = type == OrgType.supplier and current.has_platform_role(OrgRole.procurement_manager)
    if not current.is_platform_admin and not buyer:
        stmt = stmt.where(Organization.id.in_([o.id for _, o in current.memberships]))
    if type is not None:
        stmt = stmt.where(Organization.type == type)
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(Organization.legal_name.ilike(pattern), Organization.inn.like(pattern))
        )
    if cursor is not None:
        stmt = stmt.where(Organization.id < cursor)
    rows = list(
        (await db.execute(stmt.order_by(Organization.id.desc()).limit(limit + 1))).scalars()
    )
    next_cursor = str(rows[limit - 1].id) if len(rows) > limit else None
    return Page(items=[_out(o) for o in rows[:limit]], next_cursor=next_cursor)


@router.get("/{org_id}", response_model=OrgOut, summary="Карточка организации")
async def get_org(org_id: uuid.UUID, current: Auth, db: DbSession) -> OrgOut:
    org = await _get_org(db, org_id)
    if not _can_view(current, org):
        raise Problem(404, "not-found", "Организация не найдена")
    return _out(org)


@router.patch("/{org_id}", response_model=OrgOut, summary="Изменить реквизиты")
async def update_org(
    org_id: uuid.UUID, body: OrgUpdate, current: PlatformAdmin, request: Request, db: DbSession
) -> OrgOut:
    org = await _get_org(db, org_id)
    changes = body.model_dump(exclude_unset=True)
    if "bank_account" in changes and "bank_bik" not in changes and org.bank_bik:
        # Счёт проверяется вместе с БИК — подставляем текущий БИК.
        OrgUpdate(bank_bik=org.bank_bik, bank_account=changes["bank_account"])
    old = {k: getattr(org, k) for k in changes}
    for key, value in changes.items():
        setattr(org, key, value)
    if changes:
        audit(
            db,
            entity_type="organization",
            entity_id=org.id,
            action="organization.updated",
            actor_id=current.id,
            ip=client_ip(request),
            old=old,
            new=changes,
        )
        await db.commit()
        await db.refresh(org)
    return _out(org)


@router.post("/{org_id}/verify", response_model=OrgOut, summary="Отметить проверку организации")
async def verify_org(
    org_id: uuid.UUID, current: PlatformAdmin, request: Request, db: DbSession
) -> OrgOut:
    org = await _get_org(db, org_id)
    if org.verified_at is None:
        org.verified_at = utcnow()
        audit(
            db,
            entity_type="organization",
            entity_id=org.id,
            action="organization.verified",
            actor_id=current.id,
            ip=client_ip(request),
        )
        await db.commit()
        await db.refresh(org)
    return _out(org)


@router.get("/{org_id}/members", response_model=list[MemberOut], summary="Сотрудники")
async def list_members(org_id: uuid.UUID, current: Auth, db: DbSession) -> list[MemberOut]:
    org = await _get_org(db, org_id)
    if not _can_view(current, org):
        raise Problem(404, "not-found", "Организация не найдена")
    rows = await db.execute(
        select(OrgMember, User)
        .join(User, User.id == OrgMember.user_id)
        .where(OrgMember.org_id == org.id)
        .order_by(OrgMember.created_at)
    )
    return [
        MemberOut(
            id=m.id,
            user_id=u.id,
            phone=u.phone,
            full_name=u.full_name,
            role=m.role,
            created_at=m.created_at,
        )
        for m, u in rows
    ]


@router.post(
    "/{org_id}/members",
    response_model=MemberOut,
    status_code=201,
    summary="Добавить сотрудника по телефону",
)
async def add_member(
    org_id: uuid.UUID, body: MemberAdd, current: Auth, request: Request, db: DbSession
) -> MemberOut:
    org = await _get_org(db, org_id)
    if not _can_manage_members(current, org):
        raise Problem(403, "forbidden", "Добавлять сотрудников может руководитель организации")
    if body.role not in ROLES_BY_ORG_TYPE[org.type]:
        raise Problem(
            422,
            "role-not-allowed",
            "Эта роль недоступна для такой организации",
            allowed=sorted(r.value for r in ROLES_BY_ORG_TYPE[org.type]),
        )
    user, created = await get_or_create_user(db, body.phone)
    if body.full_name and not user.full_name:
        user.full_name = body.full_name.strip()
    if created:
        audit(
            db,
            entity_type="user",
            entity_id=user.id,
            action="user.invited",
            actor_id=current.id,
            ip=client_ip(request),
            new={"phone": user.phone},
        )
    duplicate = await db.execute(
        select(OrgMember.id).where(
            OrgMember.org_id == org.id, OrgMember.user_id == user.id, OrgMember.role == body.role
        )
    )
    if duplicate.first() is not None:
        raise Problem(409, "member-exists", "Этот сотрудник уже добавлен с такой ролью")
    member = OrgMember(org_id=org.id, user_id=user.id, role=body.role)
    db.add(member)
    await db.flush()
    audit(
        db,
        entity_type="org_member",
        entity_id=member.id,
        action="org_member.added",
        actor_id=current.id,
        ip=client_ip(request),
        new={"org_id": org.id, "user_id": user.id, "role": body.role},
    )
    await db.commit()
    await db.refresh(member)
    return MemberOut(
        id=member.id,
        user_id=user.id,
        phone=user.phone,
        full_name=user.full_name,
        role=member.role,
        created_at=member.created_at,
    )


@router.delete("/{org_id}/members/{member_id}", status_code=204, summary="Убрать сотрудника")
async def remove_member(
    org_id: uuid.UUID, member_id: uuid.UUID, current: Auth, request: Request, db: DbSession
) -> Response:
    org = await _get_org(db, org_id)
    if not _can_manage_members(current, org):
        raise Problem(403, "forbidden", "Убирать сотрудников может руководитель организации")
    member = await db.get(OrgMember, member_id)
    if member is None or member.org_id != org.id:
        raise Problem(404, "not-found", "Сотрудник не найден")
    if member.role in ORG_MANAGERS[org.type] | {OrgRole.platform_admin}:
        same_role = await db.scalar(
            select(func.count())
            .select_from(OrgMember)
            .where(OrgMember.org_id == org.id, OrgMember.role == member.role)
        )
        if same_role == 1:
            raise Problem(409, "last-manager", "Нельзя убрать последнего руководителя")
    await db.delete(member)
    audit(
        db,
        entity_type="org_member",
        entity_id=member.id,
        action="org_member.removed",
        actor_id=current.id,
        ip=client_ip(request),
        old={"org_id": org.id, "user_id": member.user_id, "role": member.role},
    )
    await db.commit()
    return Response(status_code=204)


class OrgDeviceOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    phone: str
    full_name: str | None
    kind: SessionKind
    user_agent: str | None
    created_at: datetime
    last_seen_at: datetime


@router.get(
    "/{org_id}/devices",
    response_model=list[OrgDeviceOut],
    summary="Устройства сотрудников (для руководителя)",
)
async def org_devices(org_id: uuid.UUID, current: Auth, db: DbSession) -> list[OrgDeviceOut]:
    org = await _get_org(db, org_id)
    if not _can_manage_members(current, org):
        raise Problem(403, "forbidden", "Устройства сотрудников видит руководитель")
    member_ids = select(OrgMember.user_id).where(OrgMember.org_id == org.id)
    rows = await db.execute(
        select(DeviceSession, User)
        .join(User, User.id == DeviceSession.user_id)
        .where(
            DeviceSession.user_id.in_(member_ids),
            DeviceSession.revoked_at.is_(None),
            DeviceSession.expires_at > utcnow(),
        )
        .order_by(DeviceSession.last_seen_at.desc())
    )
    return [
        OrgDeviceOut(
            id=d.id,
            user_id=u.id,
            phone=u.phone,
            full_name=u.full_name,
            kind=d.kind,
            user_agent=d.user_agent,
            created_at=d.created_at,
            last_seen_at=d.last_seen_at,
        )
        for d, u in rows
    ]
