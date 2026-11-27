import uuid
from datetime import datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select

from app.core.audit import audit
from app.core.deps import Auth, CurrentUser, DbSession, client_ip
from app.core.problems import Problem
from app.core.security import utcnow
from app.models import Consent, Notification
from app.models.enums import ConsentType, OrgRole, OrgType
from app.modules.auth import service as auth_service
from app.modules.notifications.service import TEMPLATES, render

router = APIRouter(prefix="/me", tags=["me"])


class MembershipOut(BaseModel):
    org_id: uuid.UUID
    org_type: OrgType
    org_name: str
    role: OrgRole


class ConsentOut(BaseModel):
    type: ConsentType
    text_version: str
    granted_at: datetime


class MeOut(BaseModel):
    id: uuid.UUID
    phone: str
    email: str | None
    full_name: str | None
    created_at: datetime
    is_platform_admin: bool
    memberships: list[MembershipOut]
    consents: list[ConsentOut]


class MeUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=200)
    email: EmailStr | None = None


async def _me_out(current: CurrentUser, db: DbSession) -> MeOut:
    consents = (
        await db.execute(
            select(Consent)
            .where(Consent.user_id == current.id, Consent.revoked_at.is_(None))
            .order_by(Consent.granted_at)
        )
    ).scalars()
    u = current.user
    return MeOut(
        id=u.id,
        phone=u.phone,
        email=u.email,
        full_name=u.full_name,
        created_at=u.created_at,
        is_platform_admin=current.is_platform_admin,
        memberships=[
            MembershipOut(org_id=o.id, org_type=o.type, org_name=o.legal_name, role=m.role)
            for m, o in current.memberships
        ],
        consents=[
            ConsentOut(type=c.type, text_version=c.text_version, granted_at=c.granted_at)
            for c in consents
        ],
    )


@router.get("", response_model=MeOut, summary="Мой профиль и роли")
async def get_me(current: Auth, db: DbSession) -> MeOut:
    return await _me_out(current, db)


@router.patch("", response_model=MeOut, summary="Изменить имя и e-mail")
async def update_me(body: MeUpdate, current: Auth, request: Request, db: DbSession) -> MeOut:
    user = current.user
    changes = body.model_dump(exclude_unset=True)
    old = {k: getattr(user, k) for k in changes}
    for key, value in changes.items():
        setattr(user, key, value.strip() if isinstance(value, str) else value)
    if changes:
        audit(
            db,
            entity_type="user",
            entity_id=user.id,
            action="user.updated",
            actor_id=user.id,
            ip=client_ip(request),
            old=old,
            new=changes,
        )
        await db.commit()
        await db.refresh(user)
    return await _me_out(current, db)


@router.post("/consents/{consent_type}", response_model=MeOut, summary="Дать согласие")
async def grant(consent_type: ConsentType, current: Auth, request: Request, db: DbSession) -> MeOut:
    if not await auth_service.has_active_consent(db, current.id, consent_type):
        auth_service.grant_consent(db, current.id, consent_type, client_ip(request))
        await db.commit()
    return await _me_out(current, db)


@router.delete("/consents/{consent_type}", response_model=MeOut, summary="Отозвать согласие")
async def revoke(
    consent_type: ConsentType, current: Auth, request: Request, db: DbSession
) -> MeOut:
    if consent_type == ConsentType.pd_processing:
        # Отзыв согласия на обработку ПДн = удаление аккаунта, это отдельный процесс (спринт 6).
        raise Problem(
            409,
            "consent-required-for-account",
            "Согласие на обработку данных нельзя отозвать отдельно",
            "Чтобы отозвать его, оформите запрос на удаление аккаунта.",
        )
    rows = (
        await db.execute(
            select(Consent).where(
                Consent.user_id == current.id,
                Consent.type == consent_type,
                Consent.revoked_at.is_(None),
            )
        )
    ).scalars()
    now = utcnow()
    for consent in rows:
        consent.revoked_at = now
        audit(
            db,
            entity_type="consent",
            entity_id=current.id,
            action=f"consent.revoked.{consent_type}",
            actor_id=current.id,
            ip=client_ip(request),
        )
    await db.commit()
    return await _me_out(current, db)


class NotificationOut(BaseModel):
    id: uuid.UUID
    text: str
    status: str
    created_at: datetime


@router.get("/notifications", response_model=list[NotificationOut], summary="Мои уведомления")
async def my_notifications(current: Auth, db: DbSession) -> list[NotificationOut]:
    rows = await db.execute(
        select(Notification)
        .where(Notification.user_id == current.id)
        .order_by(Notification.created_at.desc())
        .limit(30)
    )
    return [
        NotificationOut(
            id=n.id,
            text=render(n.template, n.payload) if n.template in TEMPLATES else n.template,
            status=n.status,
            created_at=n.created_at,
        )
        for n in rows.scalars()
    ]
