import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.validators import (
    is_valid_account,
    is_valid_bik,
    is_valid_inn,
    is_valid_kpp,
    is_valid_ogrn,
    normalize_phone,
)
from app.models.enums import OrgRole, OrgType


def _strip(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


class OrgRequisites(BaseModel):
    ogrn: str | None = None
    kpp: str | None = None
    bank_bik: str | None = None
    bank_account: str | None = None

    @field_validator("ogrn", "kpp", "bank_bik", "bank_account", mode="before")
    @classmethod
    def _clean(cls, value: str | None) -> str | None:
        return _strip(value)

    @field_validator("ogrn")
    @classmethod
    def _ogrn(cls, value: str | None) -> str | None:
        if value is not None and not is_valid_ogrn(value):
            raise ValueError("ОГРН/ОГРНИП указан с ошибкой")
        return value

    @field_validator("kpp")
    @classmethod
    def _kpp(cls, value: str | None) -> str | None:
        if value is not None and not is_valid_kpp(value):
            raise ValueError("КПП должен состоять из 9 символов")
        return value

    @field_validator("bank_bik")
    @classmethod
    def _bik(cls, value: str | None) -> str | None:
        if value is not None and not is_valid_bik(value):
            raise ValueError("БИК должен состоять из 9 цифр")
        return value

    @model_validator(mode="after")
    def _account(self) -> "OrgRequisites":
        if self.bank_account is not None:
            if self.bank_bik is None:
                raise ValueError("Для расчётного счёта укажите БИК банка")
            if not is_valid_account(self.bank_account, self.bank_bik):
                raise ValueError("Расчётный счёт не совпадает с БИК или указан с ошибкой")
        return self


class OrgCreate(OrgRequisites):
    type: OrgType
    inn: str
    legal_name: str = Field(min_length=2, max_length=300)

    @field_validator("inn", mode="before")
    @classmethod
    def _inn(cls, value: str) -> str:
        value = (value or "").strip()
        if not is_valid_inn(value):
            raise ValueError("ИНН указан с ошибкой")
        return value

    @field_validator("type")
    @classmethod
    def _type(cls, value: OrgType) -> OrgType:
        if value == OrgType.platform:
            raise ValueError("Организация платформы создаётся автоматически")
        return value


class OrgUpdate(OrgRequisites):
    legal_name: str | None = Field(default=None, min_length=2, max_length=300)
    # Код получателя в банке: администратор вносит его после регистрации организации в банке.
    bank_partner_id: str | None = Field(default=None, max_length=64)


class OrgOut(BaseModel):
    id: uuid.UUID
    type: OrgType
    inn: str
    ogrn: str | None
    kpp: str | None
    legal_name: str
    bank_bik: str | None
    bank_account: str | None
    bank_partner_id: str | None = None
    rating: Decimal | None
    sla_score: Decimal | None
    verified_at: datetime | None
    created_at: datetime


class MemberAdd(BaseModel):
    phone: str
    role: OrgRole
    full_name: str | None = Field(default=None, max_length=200)

    @field_validator("phone")
    @classmethod
    def _phone(cls, value: str) -> str:
        return normalize_phone(value)


class MemberOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    phone: str
    full_name: str | None
    role: OrgRole
    created_at: datetime
