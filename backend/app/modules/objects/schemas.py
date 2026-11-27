import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.validators import normalize_phone
from app.models.enums import AssignmentRole, ObjectStatus, StageStatus


class ObjectCreate(BaseModel):
    address_text: str = Field(min_length=5, max_length=500)
    address_fias: str | None = Field(default=None, max_length=36)
    area_m2: Decimal | None = Field(default=None, gt=0, le=2000, decimal_places=2)
    geo_lat: Decimal | None = Field(default=None, ge=-90, le=90)
    geo_lon: Decimal | None = Field(default=None, ge=-180, le=180)
    # Как искать подрядчика: подберёт платформа или заказчик выберет сам из откликов.
    match_mode: Literal["platform", "market"] = "platform"
    # Только для администратора: создать объект от имени заказчика.
    customer_phone: str | None = None

    @field_validator("address_text")
    @classmethod
    def _strip(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("customer_phone")
    @classmethod
    def _phone(cls, value: str | None) -> str | None:
        return normalize_phone(value) if value else None


class ObjectUpdate(BaseModel):
    address_text: str | None = Field(default=None, min_length=5, max_length=500)
    address_fias: str | None = Field(default=None, max_length=36)
    area_m2: Decimal | None = Field(default=None, gt=0, le=2000, decimal_places=2)
    geo_lat: Decimal | None = Field(default=None, ge=-90, le=90)
    geo_lon: Decimal | None = Field(default=None, ge=-180, le=180)


class PersonOut(BaseModel):
    id: uuid.UUID
    phone: str
    full_name: str | None


class ObjectOut(BaseModel):
    id: uuid.UUID
    status: ObjectStatus
    address_text: str
    address_fias: str | None
    area_m2: Decimal | None
    geo_lat: Decimal | None
    geo_lon: Decimal | None
    customer: PersonOut | None = None
    contractor_org_id: uuid.UUID | None
    contractor_name: str | None = None
    match_mode: str = "platform"
    created_at: datetime
    access: dict[str, bool] = Field(default_factory=dict)


class ContractorSet(BaseModel):
    contractor_org_id: uuid.UUID | None


class AssignmentCreate(BaseModel):
    user_id: uuid.UUID
    role: AssignmentRole


class AssignmentOut(BaseModel):
    id: uuid.UUID
    role: AssignmentRole
    user: PersonOut
    created_at: datetime


class StageCreate(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    stage_type: str | None = Field(default=None, max_length=50)
    is_stop_point: bool = False
    planned_start: date | None = None
    planned_end: date | None = None
    seq: int | None = Field(default=None, ge=1, le=999)

    @model_validator(mode="after")
    def _dates(self) -> "StageCreate":
        if self.planned_start and self.planned_end and self.planned_end < self.planned_start:
            raise ValueError("Дата окончания этапа раньше даты начала")
        return self


class StageUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=200)
    stage_type: str | None = Field(default=None, max_length=50)
    is_stop_point: bool | None = None
    planned_start: date | None = None
    planned_end: date | None = None


class StageOut(BaseModel):
    id: uuid.UUID
    seq: int
    title: str
    stage_type: str | None
    is_stop_point: bool
    planned_start: date | None
    planned_end: date | None
    work_budget_kop: int
    material_budget_kop: int
    progress_pct: int
    status: StageStatus
