import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.core.config import get_settings

ALGORITHM = "HS256"


def utcnow() -> datetime:
    return datetime.now(UTC)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def hmac_hex(value: str) -> str:
    """Хэш с секретом — для одноразовых кодов, чтобы их нельзя было перебрать по базе."""
    key = get_settings().jwt_secret.encode()
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()


def new_refresh_token() -> str:
    return secrets.token_urlsafe(32)


def new_otp_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def create_access_token(user_id: uuid.UUID, session_id: uuid.UUID) -> tuple[str, int]:
    settings = get_settings()
    now = utcnow()
    ttl = settings.access_token_ttl_s
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "sid": str(session_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
        "typ": "access",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM), ttl


def decode_access_token(token: str) -> dict[str, Any]:
    payload = jwt.decode(token, get_settings().jwt_secret, algorithms=[ALGORITHM])
    if payload.get("typ") != "access":
        raise jwt.InvalidTokenError("wrong token type")
    return payload
