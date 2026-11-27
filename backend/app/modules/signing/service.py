"""Простая электронная подпись по СМС-коду (63-ФЗ): код привязан к хэшу документа,
факт подписи пишется в журнал signatures с IP и браузером."""

import uuid
from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.problems import Problem
from app.core.security import hmac_hex, new_otp_code, utcnow
from app.models import Signature, User
from app.models.enums import SignatureMethod, SignedDocumentType
from app.modules.auth.sms import get_sms_sender


@dataclass
class SignRequest:
    signature: Signature
    debug_code: str | None
    expires_in: int


def _code_key(signature_id: uuid.UUID) -> str:
    return f"sign:code:{signature_id}"


async def request_signature(
    db: AsyncSession,
    redis: Redis,
    *,
    user: User,
    document_type: SignedDocumentType,
    document_id: uuid.UUID,
    document_hash: str,
    sms_text: str,
    ip: str | None,
    user_agent: str | None,
) -> SignRequest:
    s = get_settings()
    cool_key = f"sign:cool:{user.id}:{document_id}"
    cooldown = await redis.ttl(cool_key)
    if cooldown and cooldown > 0:
        raise Problem(
            429,
            "otp-cooldown",
            "Код уже отправлен",
            f"Повторно запросить код можно через {cooldown} с.",
            headers={"Retry-After": str(cooldown)},
            retry_after=cooldown,
        )
    signature = Signature(
        document_type=document_type,
        document_id=document_id,
        user_id=user.id,
        method=SignatureMethod.sms_pep,
        document_hash=document_hash,
        otp_sent_at=utcnow(),
        ip=ip,
        user_agent=(user_agent or "")[:500] or None,
    )
    db.add(signature)
    await db.flush()
    code = new_otp_code()
    await redis.set(_code_key(signature.id), hmac_hex(f"{signature.id}:{code}"), ex=s.otp_ttl_s)
    await redis.set(cool_key, "1", ex=s.otp_resend_cooldown_s)
    await get_sms_sender().send(user.phone, sms_text.format(code=code))
    return SignRequest(signature, code if s.otp_debug else None, s.otp_ttl_s)


async def confirm_signature(
    redis: Redis, signature: Signature, user: User, code: str, current_hash: str
) -> None:
    if signature.user_id != user.id or signature.signed_at is not None:
        raise Problem(409, "signature-invalid", "Подпись уже использована или чужая")
    if signature.document_hash != current_hash:
        raise Problem(
            409,
            "document-changed",
            "Документ изменился после запроса кода",
            "Запросите код ещё раз.",
        )
    key = _code_key(signature.id)
    stored = await redis.get(key)
    if stored is None:
        raise Problem(400, "otp-expired", "Код устарел", "Запросите новый код.")
    tries_key = f"{key}:tries"
    tries = await redis.incr(tries_key)
    await redis.expire(tries_key, get_settings().otp_ttl_s)
    if tries > get_settings().otp_max_attempts:
        await redis.delete(key)
        raise Problem(
            429, "otp-attempts-exceeded", "Слишком много неверных попыток", "Запросите новый код."
        )
    if stored != hmac_hex(f"{signature.id}:{code}"):
        raise Problem(
            400,
            "otp-invalid",
            "Неверный код",
            attempts_left=get_settings().otp_max_attempts - tries,
        )
    if not await redis.delete(key):
        raise Problem(400, "otp-expired", "Код устарел", "Запросите новый код.")
    signature.signed_at = utcnow()
