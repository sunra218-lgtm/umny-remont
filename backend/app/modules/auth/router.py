import uuid

from fastapi import APIRouter, Request, Response
from sqlalchemy import select

from app.core.audit import audit
from app.core.config import get_settings
from app.core.deps import Auth, CurrentUser, DbSession, client_ip
from app.core.problems import Problem
from app.core.redis import get_redis
from app.core.security import utcnow
from app.models import DeviceSession, OrgMember, User
from app.models.enums import ConsentType, OrgRole, SessionKind
from app.modules.auth import otp, service
from app.modules.auth.schemas import (
    DeviceOut,
    OtpSentOut,
    OtpVerifyIn,
    PhoneIn,
    RefreshIn,
    TokenOut,
)
from app.modules.auth.sms import get_sms_sender

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "ur_refresh"
REFRESH_COOKIE_PATH = "/api/v1/auth"


def _set_refresh_cookie(response: Response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        max_age=s.refresh_token_ttl_s,
        httponly=True,
        secure=s.cookie_secure,
        samesite="strict",
        path=REFRESH_COOKIE_PATH,
    )


@router.post("/otp", response_model=OtpSentOut, status_code=202, summary="Отправить код по СМС")
async def send_otp(body: PhoneIn, request: Request) -> OtpSentOut:
    issued = await otp.issue_otp(get_redis(), body.phone, client_ip(request))
    await get_sms_sender().send(body.phone, f"Код для входа в Умный ремонт: {issued.code}")
    return OtpSentOut(
        phone=body.phone,
        expires_in=issued.expires_in,
        resend_in=issued.resend_in,
        debug_code=issued.code if get_settings().otp_debug else None,
    )


@router.post("/otp/verify", response_model=TokenOut, summary="Войти по коду из СМС")
async def verify_otp(
    body: OtpVerifyIn, request: Request, response: Response, db: DbSession
) -> TokenOut:
    ip = client_ip(request)
    existing = (await db.execute(select(User).where(User.phone == body.phone))).scalar_one_or_none()
    needs_consent = existing is None or not await service.has_active_consent(
        db, existing.id, ConsentType.pd_processing
    )
    if needs_consent and not body.accept_pd_processing:
        # Код не тратим: пользователь отметит согласие и отправит тот же код.
        raise Problem(
            422,
            "consent-required",
            "Нужно согласие на обработку персональных данных",
            "Без согласия по 152-ФЗ мы не можем создать личный кабинет.",
        )

    await otp.check_otp(get_redis(), body.phone, body.code)

    user, created = await service.get_or_create_user(db, body.phone)
    if user.blocked_at is not None or user.deleted_at is not None:
        raise Problem(403, "user-blocked", "Доступ заблокирован администратором")
    if created:
        audit(
            db,
            entity_type="user",
            entity_id=user.id,
            action="user.registered",
            actor_id=user.id,
            ip=ip,
            new={"phone": user.phone},
        )
    if needs_consent:
        service.grant_consent(db, user.id, ConsentType.pd_processing, ip)
    if body.accept_marketing and not await service.has_active_consent(
        db, user.id, ConsentType.marketing
    ):
        service.grant_consent(db, user.id, ConsentType.marketing, ip)
    await service.ensure_bootstrap_admin(db, user)

    device, refresh = await service.open_session(
        db, user, SessionKind.web, ip, request.headers.get("user-agent")
    )
    audit(
        db,
        entity_type="device_session",
        entity_id=device.id,
        action="auth.login",
        actor_id=user.id,
        ip=ip,
        new={"kind": device.kind},
    )
    await db.commit()

    access, ttl = service.issue_access(user, device)
    _set_refresh_cookie(response, refresh)
    return TokenOut(access_token=access, expires_in=ttl, refresh_token=refresh, is_new_user=created)


@router.post("/refresh", response_model=TokenOut, summary="Продлить сеанс")
async def refresh(
    request: Request, response: Response, db: DbSession, body: RefreshIn | None = None
) -> TokenOut:
    token = (body.refresh_token if body else None) or request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise Problem(401, "refresh-invalid", "Сеанс завершён, войдите снова")
    device, user, new_refresh = await service.rotate_session(
        db,
        token,
        client_ip(request),
        request.headers.get("x-device-timestamp"),
        request.headers.get("x-device-signature"),
    )
    await db.commit()
    access, ttl = service.issue_access(user, device)
    _set_refresh_cookie(response, new_refresh)
    return TokenOut(access_token=access, expires_in=ttl, refresh_token=new_refresh)


@router.post("/logout", status_code=204, summary="Выйти на этом устройстве")
async def logout(current: Auth, request: Request, db: DbSession) -> Response:
    device = await db.get(DeviceSession, current.session_id)
    if device is not None and device.revoked_at is None:
        device.revoked_at = utcnow()
        audit(
            db,
            entity_type="device_session",
            entity_id=device.id,
            action="auth.logout",
            actor_id=current.id,
            ip=client_ip(request),
        )
        await db.commit()
    response = Response(status_code=204)
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_COOKIE_PATH)
    return response


@router.get("/devices", response_model=list[DeviceOut], summary="Мои устройства")
async def list_devices(current: Auth, db: DbSession) -> list[DeviceOut]:
    rows = (
        await db.execute(
            select(DeviceSession)
            .where(
                DeviceSession.user_id == current.id,
                DeviceSession.revoked_at.is_(None),
                DeviceSession.expires_at > utcnow(),
            )
            .order_by(DeviceSession.last_seen_at.desc())
        )
    ).scalars()
    result = []
    for d in rows:
        item = DeviceOut.model_validate(d, from_attributes=True)
        item.is_current = d.id == current.session_id
        result.append(item)
    return result


async def can_manage_devices_of(db: DbSession, current: CurrentUser, user_id: uuid.UUID) -> bool:
    """Свои устройства — всегда; устройства сотрудников — руководителю подрядчика и админу."""
    if user_id == current.id or current.is_platform_admin:
        return True
    managed_orgs = [org.id for m, org in current.memberships if m.role == OrgRole.contractor_admin]
    if not managed_orgs:
        return False
    found = await db.execute(
        select(OrgMember.id).where(OrgMember.user_id == user_id, OrgMember.org_id.in_(managed_orgs))
    )
    return found.first() is not None


@router.delete("/devices/{device_id}", status_code=204, summary="Завершить сеанс на устройстве")
async def revoke_device(
    device_id: uuid.UUID, current: Auth, request: Request, db: DbSession
) -> Response:
    device = await db.get(DeviceSession, device_id)
    if device is None or not await can_manage_devices_of(db, current, device.user_id):
        raise Problem(404, "not-found", "Устройство не найдено")
    if device.revoked_at is None:
        device.revoked_at = utcnow()
        audit(
            db,
            entity_type="device_session",
            entity_id=device.id,
            action="auth.device_revoked",
            actor_id=current.id,
            ip=client_ip(request),
            new={"user_id": device.user_id},
        )
        await db.commit()
    return Response(status_code=204)
