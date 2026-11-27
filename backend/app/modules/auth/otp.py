"""Одноразовые коды входа: хранение в Redis, лимиты на отправку и попытки."""

from dataclasses import dataclass

from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.problems import Problem
from app.core.security import hmac_hex, new_otp_code


@dataclass
class IssuedOtp:
    code: str
    expires_in: int
    resend_in: int


def _keys(phone: str) -> tuple[str, str, str, str]:
    return (f"otp:code:{phone}", f"otp:tries:{phone}", f"otp:cool:{phone}", f"otp:hour:{phone}")


async def issue_otp(redis: Redis, phone: str, ip: str | None) -> IssuedOtp:
    s = get_settings()
    code_key, tries_key, cool_key, hour_key = _keys(phone)

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
    sent_hour = await redis.incr(hour_key)
    if sent_hour == 1:
        await redis.expire(hour_key, 3600)
    if sent_hour > s.otp_max_per_hour:
        raise Problem(
            429, "otp-hour-limit", "Слишком много запросов кода", "Попробуйте снова через час."
        )
    if ip:
        ip_key = f"otp:ip:{ip}"
        sent_ip = await redis.incr(ip_key)
        if sent_ip == 1:
            await redis.expire(ip_key, 3600)
        if sent_ip > s.otp_max_per_ip_hour:
            raise Problem(429, "otp-ip-limit", "Слишком много запросов кода с этого адреса")

    code = new_otp_code()
    pipe = redis.pipeline()
    pipe.set(code_key, hmac_hex(f"{phone}:{code}"), ex=s.otp_ttl_s)
    pipe.delete(tries_key)
    pipe.set(cool_key, "1", ex=s.otp_resend_cooldown_s)
    await pipe.execute()
    return IssuedOtp(code=code, expires_in=s.otp_ttl_s, resend_in=s.otp_resend_cooldown_s)


async def check_otp(redis: Redis, phone: str, code: str) -> None:
    """Проверяет код. Код одноразовый: после успешной проверки удаляется."""
    s = get_settings()
    code_key, tries_key, _, _ = _keys(phone)
    stored = await redis.get(code_key)
    if stored is None:
        raise Problem(400, "otp-expired", "Код устарел", "Запросите новый код.")
    tries = await redis.incr(tries_key)
    await redis.expire(tries_key, s.otp_ttl_s)
    if tries > s.otp_max_attempts:
        await redis.delete(code_key)
        raise Problem(
            429, "otp-attempts-exceeded", "Слишком много неверных попыток", "Запросите новый код."
        )
    if stored != hmac_hex(f"{phone}:{code}"):
        left = s.otp_max_attempts - tries
        raise Problem(400, "otp-invalid", "Неверный код", attempts_left=left)
    # Атомарно забираем код: параллельная проверка того же кода не пройдёт.
    if not await redis.delete(code_key):
        raise Problem(400, "otp-expired", "Код устарел", "Запросите новый код.")
    await redis.delete(tries_key)
