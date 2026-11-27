import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import device_crypto
from app.core.audit import audit
from app.core.config import get_settings
from app.core.problems import Problem
from app.core.security import create_access_token, new_refresh_token, sha256_hex, utcnow
from app.models import Consent, DeviceSession, Organization, OrgMember, User
from app.models.enums import ConsentType, OrgRole, OrgType, SessionKind


async def get_or_create_user(db: AsyncSession, phone: str) -> tuple[User, bool]:
    user = (await db.execute(select(User).where(User.phone == phone))).scalar_one_or_none()
    if user is not None:
        return user, False
    user = User(phone=phone)
    db.add(user)
    await db.flush()
    return user, True


async def has_active_consent(db: AsyncSession, user_id: uuid.UUID, kind: ConsentType) -> bool:
    found = await db.execute(
        select(Consent.id).where(
            Consent.user_id == user_id, Consent.type == kind, Consent.revoked_at.is_(None)
        )
    )
    return found.first() is not None


def grant_consent(db: AsyncSession, user_id: uuid.UUID, kind: ConsentType, ip: str | None) -> None:
    db.add(
        Consent(
            user_id=user_id,
            type=kind,
            text_version=get_settings().consent_text_version,
            ip=ip,
        )
    )
    audit(
        db,
        entity_type="consent",
        entity_id=user_id,
        action=f"consent.granted.{kind}",
        actor_id=user_id,
        ip=ip,
        new={"type": kind, "text_version": get_settings().consent_text_version},
    )


async def get_platform_org(db: AsyncSession) -> Organization:
    org = (
        (await db.execute(select(Organization).where(Organization.type == OrgType.platform)))
        .scalars()
        .first()
    )
    if org is None:
        s = get_settings()
        org = Organization(
            type=OrgType.platform, inn=s.platform_inn, legal_name=s.platform_legal_name
        )
        db.add(org)
        await db.flush()
        audit(
            db,
            entity_type="organization",
            entity_id=org.id,
            action="organization.created",
            actor_id=None,
            is_system=True,
            new={"type": org.type, "legal_name": org.legal_name},
        )
    return org


async def ensure_bootstrap_admin(db: AsyncSession, user: User) -> None:
    """Телефоны из BOOTSTRAP_ADMIN_PHONES становятся администраторами платформы при входе."""
    if user.phone not in get_settings().bootstrap_admin_phones:
        return
    org = await get_platform_org(db)
    exists = await db.execute(
        select(OrgMember.id).where(
            OrgMember.org_id == org.id,
            OrgMember.user_id == user.id,
            OrgMember.role == OrgRole.platform_admin,
        )
    )
    if exists.first() is None:
        member = OrgMember(org_id=org.id, user_id=user.id, role=OrgRole.platform_admin)
        db.add(member)
        await db.flush()
        audit(
            db,
            entity_type="org_member",
            entity_id=member.id,
            action="org_member.added",
            actor_id=None,
            is_system=True,
            new={"org_id": org.id, "user_id": user.id, "role": OrgRole.platform_admin},
        )


async def open_session(
    db: AsyncSession,
    user: User,
    kind: SessionKind,
    ip: str | None,
    user_agent: str | None,
    public_key: str | None = None,
) -> tuple[DeviceSession, str]:
    refresh = new_refresh_token()
    device = DeviceSession(
        user_id=user.id,
        kind=kind,
        device_public_key=public_key,
        device_key_hash=device_crypto.key_fingerprint(public_key) if public_key else None,
        refresh_token_hash=sha256_hex(refresh),
        ip=ip,
        user_agent=(user_agent or "")[:500] or None,
        expires_at=utcnow() + timedelta(seconds=get_settings().refresh_token_ttl_s),
    )
    db.add(device)
    await db.flush()
    return device, refresh


def check_device_signature(
    device: DeviceSession, timestamp: str | None, signature: str | None
) -> None:
    """Сессия Lite Web продлевается только с подписью ключом устройства: украденного
    refresh-токена без самого телефона недостаточно."""
    if not timestamp or not signature or not device.device_public_key:
        raise Problem(401, "device-signature-required", "Нужна подпись устройства")
    try:
        ts_ms = int(timestamp)
    except ValueError:
        raise Problem(401, "device-signature-invalid", "Подпись устройства не прошла") from None
    skew = abs(utcnow().timestamp() * 1000 - ts_ms) / 1000
    if skew > get_settings().device_signature_skew_s:
        raise Problem(
            401,
            "device-clock-skew",
            "Проверьте время на телефоне",
            "Часы устройства сильно расходятся с реальным временем.",
        )
    if not device_crypto.verify(device.device_public_key, f"refresh:{ts_ms}".encode(), signature):
        raise Problem(401, "device-signature-invalid", "Подпись устройства не прошла")


async def rotate_session(
    db: AsyncSession,
    refresh_token: str,
    ip: str | None,
    device_timestamp: str | None = None,
    device_signature: str | None = None,
) -> tuple[DeviceSession, User, str]:
    """Меняет refresh-токен на новый и продлевает сессию (скользящий срок 30 дней)."""
    row = (
        await db.execute(
            select(DeviceSession, User)
            .join(User, User.id == DeviceSession.user_id)
            .where(DeviceSession.refresh_token_hash == sha256_hex(refresh_token))
            .with_for_update()
        )
    ).first()
    if row is None:
        raise Problem(401, "refresh-invalid", "Сеанс завершён, войдите снова")
    device, user = row
    now = utcnow()
    if device.revoked_at is not None or device.expires_at <= now:
        raise Problem(401, "refresh-invalid", "Сеанс завершён, войдите снова")
    if user.blocked_at is not None or user.deleted_at is not None:
        raise Problem(403, "user-blocked", "Доступ заблокирован администратором")
    if device.kind == SessionKind.lite:
        check_device_signature(device, device_timestamp, device_signature)
    new_refresh = new_refresh_token()
    device.refresh_token_hash = sha256_hex(new_refresh)
    device.last_seen_at = now
    device.expires_at = now + timedelta(seconds=get_settings().refresh_token_ttl_s)
    if ip:
        device.ip = ip
    return device, user, new_refresh


def issue_access(user: User, device: DeviceSession) -> tuple[str, int]:
    return create_access_token(user.id, device.id)
