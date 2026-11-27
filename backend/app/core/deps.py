import uuid
from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.problems import Problem
from app.core.security import decode_access_token, utcnow
from app.models import DeviceSession, Organization, OrgMember, User
from app.models.enums import OrgRole, OrgType

DbSession = Annotated[AsyncSession, Depends(get_session)]


def client_ip(request: Request) -> str | None:
    # За обратным прокси uvicorn запускается с --proxy-headers, и client.host уже реальный.
    return request.client.host if request.client else None


@dataclass
class CurrentUser:
    user: User
    session_id: uuid.UUID
    memberships: list[tuple[OrgMember, Organization]]

    @property
    def id(self) -> uuid.UUID:
        return self.user.id

    def has_platform_role(self, *roles: OrgRole) -> bool:
        return any(
            org.type == OrgType.platform and member.role in roles
            for member, org in self.memberships
        )

    @property
    def is_platform_admin(self) -> bool:
        return self.has_platform_role(OrgRole.platform_admin)

    def roles_in_org(self, org_id: uuid.UUID) -> set[OrgRole]:
        return {member.role for member, org in self.memberships if org.id == org_id}


_UNAUTHORIZED = Problem(
    401, "unauthorized", "Требуется вход", headers={"WWW-Authenticate": "Bearer"}
)


async def get_current_user(request: Request, db: DbSession) -> CurrentUser:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise _UNAUTHORIZED
    try:
        payload = decode_access_token(token)
        user_id = uuid.UUID(payload["sub"])
        session_id = uuid.UUID(payload["sid"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        raise _UNAUTHORIZED from None

    row = (
        await db.execute(
            select(User, DeviceSession)
            .join(DeviceSession, DeviceSession.user_id == User.id)
            .where(User.id == user_id, DeviceSession.id == session_id)
        )
    ).first()
    if row is None:
        raise _UNAUTHORIZED
    user, device_session = row
    if device_session.revoked_at is not None or device_session.expires_at <= utcnow():
        raise Problem(401, "session-revoked", "Сеанс завершён, войдите снова")
    if user.blocked_at is not None or user.deleted_at is not None:
        raise Problem(403, "user-blocked", "Доступ заблокирован администратором")

    memberships = (
        await db.execute(
            select(OrgMember, Organization)
            .join(Organization, Organization.id == OrgMember.org_id)
            .where(OrgMember.user_id == user.id, Organization.deleted_at.is_(None))
        )
    ).all()
    return CurrentUser(user, session_id, [(m, o) for m, o in memberships])


Auth = Annotated[CurrentUser, Depends(get_current_user)]


async def require_platform_admin(current: Auth) -> CurrentUser:
    if not current.is_platform_admin:
        raise Problem(403, "forbidden", "Нужны права администратора платформы")
    return current


PlatformAdmin = Annotated[CurrentUser, Depends(require_platform_admin)]
