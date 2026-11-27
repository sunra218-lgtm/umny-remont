import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator

from app.models.enums import EstimateItemCategory, EstimateType


class EstimateCreate(BaseModel):
    type: EstimateType
    # Скопировать позиции из этой версии. По умолчанию — из последней версии того же типа,
    # а для первой полной сметы — из последней предварительной.
    copy_from_id: uuid.UUID | None = None
    empty: bool = False


class EstimateUpdate(BaseModel):
    reserve_kop: int = Field(ge=0, le=100_000_000_000)


class ItemIn(BaseModel):
    category: EstimateItemCategory | None = None
    title: str | None = Field(default=None, min_length=2, max_length=500)
    unit: str | None = Field(default=None, min_length=1, max_length=20)
    qty: Decimal = Field(gt=0, le=1_000_000, decimal_places=3)
    unit_price_kop: int | None = Field(default=None, ge=0, le=10_000_000_000)
    stage_id: uuid.UUID | None = None
    room: str | None = Field(default=None, max_length=100)
    price_item_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _source(self) -> "ItemIn":
        if self.price_item_id is None:
            missing = [
                name
                for name in ("category", "title", "unit", "unit_price_kop")
                if getattr(self, name) is None
            ]
            if missing:
                raise ValueError(
                    "Выберите позицию из справочника или заполните тип, наименование, "
                    "единицу и цену"
                )
        return self


class ItemUpdate(BaseModel):
    category: EstimateItemCategory | None = None
    title: str | None = Field(default=None, min_length=2, max_length=500)
    unit: str | None = Field(default=None, min_length=1, max_length=20)
    qty: Decimal | None = Field(default=None, gt=0, le=1_000_000, decimal_places=3)
    unit_price_kop: int | None = Field(default=None, ge=0, le=10_000_000_000)
    stage_id: uuid.UUID | None = None
    room: str | None = Field(default=None, max_length=100)


class ItemOut(BaseModel):
    id: uuid.UUID
    seq: int
    stage_id: uuid.UUID | None
    room: str | None
    category: EstimateItemCategory
    title: str
    unit: str
    qty: Decimal
    unit_price_kop: int
    total_kop: int
    price_item_id: uuid.UUID | None


class EstimateSummary(BaseModel):
    id: uuid.UUID
    object_id: uuid.UUID
    type: EstimateType
    version: int
    total_work_kop: int
    total_material_kop: int
    reserve_kop: int
    total_kop: int
    status: str
    created_at: datetime
    submitted_at: datetime | None
    approved_at: datetime | None


class EstimateOut(EstimateSummary):
    items: list[ItemOut]
    can_edit: bool = False
    can_approve: bool = False


class SignRequestOut(BaseModel):
    signature_id: uuid.UUID
    expires_in: int
    debug_code: str | None = None


class SignConfirmIn(BaseModel):
    signature_id: uuid.UUID
    code: str = Field(pattern=r"^\d{6}$")


class ImportRowError(BaseModel):
    row: int
    message: str


class ImportResult(BaseModel):
    imported: int
    stages_created: int
    estimate: EstimateOut
