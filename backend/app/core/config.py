from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEV_JWT_SECRET = "dev-only-secret-change-me-in-production-0123456789"  # noqa: S105


class Settings(BaseSettings):
    """Настройки приложения. Все значения читаются из переменных окружения."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: Literal["dev", "test", "prod"] = "dev"
    database_url: str = "postgresql+asyncpg://umny:umny@localhost:5432/umny"
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret: str = DEV_JWT_SECRET
    access_token_ttl_s: int = 15 * 60
    refresh_token_ttl_s: int = 30 * 24 * 3600

    otp_ttl_s: int = 5 * 60
    otp_resend_cooldown_s: int = 60
    otp_max_per_hour: int = 5
    otp_max_attempts: int = 5
    otp_max_per_ip_hour: int = 30
    # В режиме разработки код из СМС возвращается в ответе API и показывается на экране.
    otp_debug: bool = False

    sms_provider: Literal["console"] = "console"

    cookie_secure: bool = False
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # Телефоны, которые при входе автоматически получают роль администратора платформы.
    bootstrap_admin_phones: Annotated[list[str], NoDecode] = Field(default_factory=list)
    platform_inn: str = "0000000000"
    platform_legal_name: str = "ООО «Умный ремонт»"

    # Цены выездов по умолчанию (бизнес-план 2.3: 5,9–8,9 тыс. ₽).
    # Админ может изменить цену при назначении выезда.
    price_acceptance_kop: int = 740_000
    price_survey_kop: int = 740_000

    # Банк безопасной сделки. fake — имитация для разработки и тестов:
    # заказчик «оплачивает» резерв на тестовой странице сайта.
    # Настоящий банк подключается отдельным классом в app/modules/payments/bank.py.
    bank_backend: Literal["fake"] = "fake"
    bank_webhook_secret: str = "dev-bank-webhook-secret"  # noqa: S105
    bank_hold_ttl_s: int = 3 * 24 * 3600
    # Комиссия платформы с этапа в сотых долях процента (100 = 1 %).
    # Ставку задаёт владелец проекта.
    platform_fee_bp: int = 0

    # Адрес сайта: из него собираются ссылки для входа бригад.
    public_base_url: str = "http://localhost:3000"
    auth_link_ttl_s: int = 30 * 60

    # Хранилище фото: local — папка на диске (тесты, разработка без MinIO),
    # s3 — S3-совместимое хранилище (в продакшене — в РФ).
    storage_backend: Literal["local", "s3"] = "local"
    storage_local_dir: str = "/tmp/umny-media"  # noqa: S108
    s3_endpoint: str | None = None
    s3_bucket: str = "umny-media"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str = "ru-central1"
    media_link_ttl_s: int = 15 * 60
    max_photo_bytes: int = 2 * 1024 * 1024

    # Антифрод фотоотчётов (ТЗ 2.3, раздел 6.2).
    capture_ttl_s: int = 10 * 60
    geo_max_distance_m: int = 300
    geo_min_accuracy_m: int = 100
    phash_max_distance: int = 6
    device_signature_skew_s: int = 5 * 60

    consent_text_version: str = "2026-11-v1"
    idempotency_ttl_s: int = 24 * 3600

    @field_validator("cors_origins", "bootstrap_admin_phones", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def _check_prod(self) -> "Settings":
        if self.env == "prod":
            if self.jwt_secret == DEV_JWT_SECRET or len(self.jwt_secret) < 32:
                raise ValueError("В prod нужен собственный JWT_SECRET длиной от 32 символов")
            if self.otp_debug:
                raise ValueError("OTP_DEBUG нельзя включать в prod")
            if not self.cookie_secure:
                raise ValueError("В prod COOKIE_SECURE должен быть true")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
