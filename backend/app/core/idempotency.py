"""Заголовок Idempotency-Key для изменяющих запросов (ТЗ 2.3, раздел 7).

Повтор запроса с тем же ключом возвращает сохранённый ответ и не выполняет действие второй раз.
Ответы хранятся в Redis 24 часа. Вход (/auth) и вебхуки банка идут без ключа: у них своя защита.
"""

import hashlib
import json
import re
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import get_settings
from app.core.problems import problem_response
from app.core.redis import get_redis

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
EXEMPT_PREFIXES = ("/api/v1/auth/", "/api/v1/webhooks/")
KEY_RE = re.compile(r"^[A-Za-z0-9_\-:.]{8,100}$")
PENDING = "__pending__"


class IdempotencyMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope["path"]
        method: str = scope["method"]
        if (
            method not in MUTATING
            or not path.startswith("/api/v1/")
            or path.startswith(EXEMPT_PREFIXES)
        ):
            await self.app(scope, receive, send)
            return

        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        key = headers.get("idempotency-key")
        if not key or not KEY_RE.match(key):
            await problem_response(
                400,
                "idempotency-key-required",
                "Нужен заголовок Idempotency-Key",
                "Каждый изменяющий запрос должен передавать уникальный Idempotency-Key "
                "(например, UUID).",
                path,
            )(scope, receive, send)
            return

        body = b""
        more = True
        while more:
            message = await receive()
            body += message.get("body", b"")
            more = message.get("more_body", False)

        who = headers.get("authorization") or f"anon:{scope.get('client', ('', 0))[0]}"
        scope_hash = hashlib.sha256(who.encode()).hexdigest()[:32]
        request_hash = hashlib.sha256(method.encode() + path.encode() + b"\n" + body).hexdigest()
        redis_key = f"idem:{scope_hash}:{key}"
        redis = get_redis()
        ttl = get_settings().idempotency_ttl_s

        acquired = await redis.set(redis_key, PENDING, nx=True, ex=ttl)
        if not acquired:
            stored = await redis.get(redis_key)
            if stored == PENDING:
                await problem_response(
                    409,
                    "idempotency-in-progress",
                    "Запрос с этим ключом ещё выполняется",
                    None,
                    path,
                )(scope, receive, send)
                return
            saved: dict[str, Any] = json.loads(stored or "{}")
            if saved.get("request_hash") != request_hash:
                await problem_response(
                    422,
                    "idempotency-key-reused",
                    "Этот Idempotency-Key уже использован для другого запроса",
                    None,
                    path,
                )(scope, receive, send)
                return
            await send(
                {
                    "type": "http.response.start",
                    "status": saved["status"],
                    "headers": [
                        (b"content-type", saved["content_type"].encode()),
                        (b"idempotent-replay", b"true"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": saved["body"].encode()})
            return

        sent_body = False

        async def replay_receive() -> Message:
            nonlocal sent_body
            if not sent_body:
                sent_body = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        captured: dict[str, Any] = {"status": 500, "content_type": "application/json", "body": b""}

        async def capture_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                captured["status"] = message["status"]
                for k, v in message.get("headers", []):
                    if k.lower() == b"content-type":
                        captured["content_type"] = v.decode()
            elif message["type"] == "http.response.body":
                captured["body"] += message.get("body", b"")
            await send(message)

        try:
            await self.app(scope, replay_receive, capture_send)
        except Exception:
            await redis.delete(redis_key)
            raise

        if captured["status"] >= 500:
            # Ошибку сервера можно повторить с тем же ключом.
            await redis.delete(redis_key)
            return
        await redis.set(
            redis_key,
            json.dumps(
                {
                    "request_hash": request_hash,
                    "status": captured["status"],
                    "content_type": captured["content_type"],
                    "body": captured["body"].decode("utf-8", errors="replace"),
                }
            ),
            ex=ttl,
        )
