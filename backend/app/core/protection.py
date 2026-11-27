"""Защита API на уровне всех запросов: заголовки безопасности и ограничение частоты.

Ограничение — фиксированное окно в Redis по IP-адресу: не больше RATE_LIMIT_PER_MINUTE запросов
в минуту и RATE_LIMIT_WRITES_PER_MINUTE изменяющих. Этого с запасом хватает живому человеку и
бригаде за одним роутером, но останавливает перебор и случайные циклы в клиенте. Если Redis
недоступен, запросы не блокируются: доступность важнее.
"""

import logging
import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import get_settings
from app.core.problems import problem_response
from app.core.redis import get_redis

log = logging.getLogger("umny.protection")

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
EXEMPT = ("/api/v1/health", "/api/v1/webhooks/")

BASE_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    (b"cross-origin-opener-policy", b"same-origin"),
]
# Ответы API — данные, а не страницы: запрещаем встраивать и исполнять что-либо.
API_CSP = (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'")
HSTS = (b"strict-transport-security", b"max-age=31536000; includeSubDomains")


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        extra = list(BASE_HEADERS)
        if not scope["path"].startswith("/api/docs"):
            extra.append(API_CSP)
        if get_settings().env == "prod":
            extra.append(HSTS)
        # Личные данные не должны оседать в общих кэшах прокси.
        cache = (b"cache-control", b"no-store")

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {k.lower() for k, _ in headers}
                headers += [(k, v) for k, v in extra if k not in present]
                if b"cache-control" not in present:
                    headers.append(cache)
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


class RateLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        s = get_settings()
        path: str = scope.get("path", "")
        if (
            scope["type"] != "http"
            or s.rate_limit_per_minute <= 0
            or not path.startswith("/api/")
            or path.startswith(EXEMPT)
        ):
            await self.app(scope, receive, send)
            return
        ip = (scope.get("client") or ("unknown", 0))[0]
        window = int(time.time() // 60)
        checks = [("all", s.rate_limit_per_minute)]
        if scope["method"] in MUTATING:
            checks.append(("write", s.rate_limit_writes_per_minute))
        try:
            redis = get_redis()
            for kind, limit in checks:
                key = f"rl:{kind}:{ip}:{window}"
                count = await redis.incr(key)
                if count == 1:
                    await redis.expire(key, 90)
                if count > limit:
                    retry = str(60 - int(time.time()) % 60)
                    log.warning("Превышен лимит запросов: %s %s", ip, kind)
                    await problem_response(
                        429,
                        "rate-limited",
                        "Слишком много запросов",
                        "Подождите минуту и попробуйте снова.",
                        headers={"Retry-After": retry},
                    )(scope, receive, send)
                    return
        except Exception:  # noqa: BLE001 — без Redis работаем без лимита, но пишем в журнал
            log.exception("Лимит запросов не проверен")
        await self.app(scope, receive, send)
