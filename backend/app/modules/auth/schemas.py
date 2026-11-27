import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.core.validators import normalize_phone
from app.models.enums import SessionKind


class PhoneIn(BaseModel):
    phone: str = Field(examples=["+7 999 123-45-67"])

    @field_validator("phone")
    @classmethod
    def _phone(cls, value: str) -> str:
        return normalize_phone(value)


class OtpSentOut(BaseModel):
    phone: str
    expires_in: int
    resend_in: int
    # Только в режиме разработки (OTP_DEBUG=true): код, чтобы войти без настоящей СМС.
    debug_code: str | None = None


class OtpVerifyIn(PhoneIn):
    code: str = Field(pattern=r"^\d{6}$")
    accept_pd_processing: bool = False
    accept_marketing: bool = False


class RefreshIn(BaseModel):
    refresh_token: str | None = None


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105
    expires_in: int
    refresh_token: str
    is_new_user: bool = False


class DeviceOut(BaseModel):
    id: uuid.UUID
    kind: SessionKind
    user_agent: str | None
    ip: str | None
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    is_current: bool = False

    @field_validator("ip", mode="before")
    @classmethod
    def _ip(cls, value: object) -> str | None:
        return str(value) if value is not None else None
