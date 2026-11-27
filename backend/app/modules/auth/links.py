"""Вход бригады по одноразовой ссылке (ТЗ 2.3, раздел 6.1).

Руководитель подрядчика выдаёт прорабу ссылку (СМС), она живёт 30 минут и работает один раз.
При переходе телефон создаёт неэкспортируемую ключевую пару, сервер запоминает открытый ключ,
и дальше сессия на этом устройстве живёт 30 дней с продлением, пока её не отзовут.
"""

import secrets
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.access import load_object
from app.core.audit import audit
from app.core.config import get_settings
from app.core.deps import Auth, DbSession, client_ip
from app.core.device_crypto import BadPublicKey, load_public_key
from app.core.problems import Problem
from app.core.security import sha256_hex, utcnow
from app.models import AuthLink, OrgMember, User
from app.models.enums import AssignmentRole, OrgRole, SessionKind
from app.modules.auth import service
from app.modules.auth.router import _set_refresh_cookie
from app.modules.auth.sms import get_sms_sender
from app.modules.objects.router import ensure_assignment

router = APIRouter(prefix="/auth/links", tags=["auth"])


class LinkCreate(BaseModel):
    user_id: uuid.UUID
    object_id: uuid.UUID


class LinkOut(BaseModel):
    # Ссылку видит только прораб в СМС; руководителю она показывается лишь в режиме разработки,
    # иначе он мог бы войти вместо прораба.
    debug_url: str | None = None
    expires_at: datetime
    phone: str


class ExchangeIn(BaseModel):
    public_key: str = Field(min_length=40, max_length=400, description="SPKI, base64")


class ExchangeOut(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105
    expires_in: int
    refresh_token: str
    session_id: uuid.UUID
    object_id: uuid.UUID | None
    full_name: str | None


@router.post("", response_model=LinkOut, status_code=201, summary="Выдать прорабу ссылку входа")
async def create_link(body: LinkCreate, current: Auth, request: Request, db: DbSession) -> LinkOut:
    access = await load_object(db, current, body.object_id)
    if not access.can_manage_team:
        raise Problem(403, "forbidden", "Ссылку выдаёт руководитель подрядчика")
    obj = access.obj
    if obj.contractor_org_id is None:
        raise Problem(409, "no-contractor", "На объекте ещё нет подрядчика")
    is_foreman = (
        await db.execute(
            select(OrgMember.id).where(
                OrgMember.org_id == obj.contractor_org_id,
                OrgMember.user_id == body.user_id,
                OrgMember.role == OrgRole.foreman,
            )
        )
    ).first()
    user = await db.get(User, body.user_id)
    if is_foreman is None or user is None:
        raise Problem(422, "not-a-foreman", "Это не прораб вашей организации")
    if user.blocked_at is not None:
        raise Problem(409, "user-blocked", "Пользователь заблокирован")
    await ensure_assignment(db, obj.id, user.id, AssignmentRole.foreman)

    token = secrets.token_urlsafe(24)
    link = AuthLink(
        token_hash=sha256_hex(token),
        user_id=user.id,
        object_id=obj.id,
        created_by=current.id,
        expires_at=utcnow() + timedelta(seconds=get_settings().auth_link_ttl_s),
        created_ip=client_ip(request),
    )
    db.add(link)
    await db.flush()
    audit(
        db,
        entity_type="auth_link",
        entity_id=link.id,
        action="auth.link_issued",
        actor_id=current.id,
        ip=client_ip(request),
        new={"user_id": user.id, "object_id": obj.id},
    )
    await db.commit()
    url = f"{get_settings().public_base_url.rstrip('/')}/l/{token}"
    await get_sms_sender().send(
        user.phone, f"Умный ремонт: вход для бригады, ссылка действует 30 минут: {url}"
    )
    return LinkOut(
        debug_url=url if get_settings().otp_debug else None,
        expires_at=link.expires_at,
        phone=user.phone,
    )


@router.post(
    "/{token}/exchange",
    response_model=ExchangeOut,
    summary="Войти по ссылке и привязать устройство",
)
async def exchange(
    token: str, body: ExchangeIn, request: Request, response: Response, db: DbSession
) -> ExchangeOut:
    try:
        load_public_key(body.public_key)
    except BadPublicKey as exc:
        raise Problem(422, "bad-public-key", str(exc)) from None
    link = (
        await db.execute(
            select(AuthLink).where(AuthLink.token_hash == sha256_hex(token)).with_for_update()
        )
    ).scalar_one_or_none()
    # Одинаковый ответ для «нет такой», «устарела» и «уже использована»: ссылку не подобрать.
    gone = Problem(
        410,
        "link-invalid",
        "Ссылка больше не действует",
        "Попросите руководителя прислать новую ссылку.",
    )
    if link is None or link.used_at is not None or link.expires_at <= utcnow():
        raise gone
    user = await db.get(User, link.user_id)
    if user is None or user.blocked_at is not None or user.deleted_at is not None:
        raise gone
    link.used_at = utcnow()
    ip = client_ip(request)
    device, refresh = await service.open_session(
        db, user, SessionKind.lite, ip, request.headers.get("user-agent"), body.public_key
    )
    audit(
        db,
        entity_type="device_session",
        entity_id=device.id,
        action="auth.lite_login",
        actor_id=user.id,
        ip=ip,
        new={"link_id": link.id, "object_id": link.object_id},
    )
    await db.commit()
    access, ttl = service.issue_access(user, device)
    _set_refresh_cookie(response, refresh)
    return ExchangeOut(
        access_token=access,
        expires_in=ttl,
        refresh_token=refresh,
        session_id=device.id,
        object_id=link.object_id,
        full_name=user.full_name,
    )
