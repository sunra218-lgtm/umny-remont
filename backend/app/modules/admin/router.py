import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, field_validator
from sqlalchemy import or_, select, update

from app.core.audit import audit
from app.core.deps import DbSession, PlatformAdmin, client_ip
from app.core.pagination import Page
from app.core.problems import Problem
from app.core.security import utcnow
from app.models import AuditLog, DeviceSession, User

router = APIRouter(prefix="/admin", tags=["admin"])


class AdminUserOut(BaseModel):
    id: uuid.UUID
    phone: str
    email: str | None
    full_name: str | None
    created_at: datetime
    blocked_at: datetime | None


class AuditOut(BaseModel):
    id: int
    created_at: datetime
    entity_type: str
    entity_id: uuid.UUID | None
    actor_id: uuid.UUID | None
    action: str
    old_val: dict[str, Any] | None
    new_val: dict[str, Any] | None
    ip: str | None
    is_system: bool

    @field_validator("ip", mode="before")
    @classmethod
    def _ip(cls, value: object) -> str | None:
        return str(value) if value is not None else None


@router.get("/users", response_model=Page[AdminUserOut], summary="Пользователи")
async def list_users(
    _: PlatformAdmin,
    db: DbSession,
    q: str | None = Query(default=None, max_length=100),
    cursor: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> Page[AdminUserOut]:
    stmt = select(User).where(User.deleted_at.is_(None))
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(User.phone.like(pattern), User.full_name.ilike(pattern), User.email.ilike(pattern))
        )
    if cursor is not None:
        stmt = stmt.where(User.id < cursor)
    rows = list((await db.execute(stmt.order_by(User.id.desc()).limit(limit + 1))).scalars())
    next_cursor = str(rows[limit - 1].id) if len(rows) > limit else None
    return Page(
        items=[AdminUserOut.model_validate(u, from_attributes=True) for u in rows[:limit]],
        next_cursor=next_cursor,
    )


async def _set_blocked(
    user_id: uuid.UUID, blocked: bool, current: PlatformAdmin, request: Request, db: DbSession
) -> AdminUserOut:
    user = await db.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise Problem(404, "not-found", "Пользователь не найден")
    if blocked and user.id == current.id:
        raise Problem(409, "cannot-block-self", "Нельзя заблокировать самого себя")
    if blocked and user.blocked_at is None:
        user.blocked_at = utcnow()
        # Блокировка сразу завершает все сеансы пользователя.
        await db.execute(
            update(DeviceSession)
            .where(DeviceSession.user_id == user.id, DeviceSession.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )
        audit(
            db,
            entity_type="user",
            entity_id=user.id,
            action="user.blocked",
            actor_id=current.id,
            ip=client_ip(request),
        )
    elif not blocked and user.blocked_at is not None:
        user.blocked_at = None
        audit(
            db,
            entity_type="user",
            entity_id=user.id,
            action="user.unblocked",
            actor_id=current.id,
            ip=client_ip(request),
        )
    await db.commit()
    await db.refresh(user)
    return AdminUserOut.model_validate(user, from_attributes=True)


@router.post("/users/{user_id}/block", response_model=AdminUserOut, summary="Заблокировать")
async def block_user(
    user_id: uuid.UUID, current: PlatformAdmin, request: Request, db: DbSession
) -> AdminUserOut:
    return await _set_blocked(user_id, True, current, request, db)


@router.post("/users/{user_id}/unblock", response_model=AdminUserOut, summary="Разблокировать")
async def unblock_user(
    user_id: uuid.UUID, current: PlatformAdmin, request: Request, db: DbSession
) -> AdminUserOut:
    return await _set_blocked(user_id, False, current, request, db)


@router.get("/audit-logs", response_model=Page[AuditOut], summary="Журнал действий")
async def list_audit(
    _: PlatformAdmin,
    db: DbSession,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    cursor: int | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> Page[AuditOut]:
    stmt = select(AuditLog)
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if actor_id:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
    if cursor is not None:
        stmt = stmt.where(AuditLog.id < cursor)
    rows = list((await db.execute(stmt.order_by(AuditLog.id.desc()).limit(limit + 1))).scalars())
    next_cursor = str(rows[limit - 1].id) if len(rows) > limit else None
    return Page(
        items=[AuditOut.model_validate(r, from_attributes=True) for r in rows[:limit]],
        next_cursor=next_cursor,
    )
