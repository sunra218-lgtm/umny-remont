from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEV_JWT_SECRET = "dev-only-secret-change-me-in-production-0123456789"  # noqa: S105
DEV_BANK_SECRET = "dev-bank-webhook-secret"  # noqa: S105


class Settings(BaseSettings):
    """Настройки приложения. Все значения читаются из переменных окружения."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: Literal["dev", "test", "prod"] = "dev"
    database_url: str = "postgresql+asyncpg://umny:umny@localhost:5432/umny"
    redis_url: str = "redis://localhost:6379/0"
    # Соединений с базой на один процесс uvicorn (по нагрузочному тесту спринта 7).
    db_pool_size: int = 10
    db_max_overflow: int = 10

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
    # tbank — Т-Банк: интернет-эквайринг с безопасной сделкой и выплаты (e2c).
    bank_backend: Literal["fake", "tbank"] = "fake"
    bank_webhook_secret: str = DEV_BANK_SECRET
    bank_hold_ttl_s: int = 3 * 24 * 3600
    # Т-Банк. Терминал приёма оплаты и терминал выплат выдаёт банк по договору;
    # у тестовых терминалов в конце ключа стоит DEMO. Ключи — только в .env на сервере.
    tbank_api_url: str = "https://securepay.tinkoff.ru/v2"
    tbank_payout_api_url: str = "https://securepay.tinkoff.ru/e2c/v2"
    tbank_terminal_key: str | None = None
    tbank_password: str | None = None
    tbank_payout_terminal_key: str | None = None
    tbank_payout_password: str | None = None
    # Код платформы как получателя выплат (маржа, комиссия).
    tbank_platform_partner_id: str | None = None
    tbank_timeout_s: float = 20.0
    # Комиссия платформы с этапа в сотых долях процента (100 = 1 %).
    # Ставку задаёт владелец проекта.
    platform_fee_bp: int = 0

    # Помощник мастера. search — ответ подбирается из техкарт без нейросети;
    # yandexgpt — YandexGPT (Яндекс Облако, данные остаются в РФ) пишет ответ по найденным
    # шагам техкарт. Ключ сервисного аккаунта — только в .env на сервере.
    assistant_backend: Literal["search", "yandexgpt"] = "search"
    yandex_folder_id: str | None = None
    yandex_api_key: str | None = None
    yandex_gpt_model: str = "yandexgpt-lite/latest"

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

    # Ограничение частоты запросов с одного IP (0 — выключено).
    rate_limit_per_minute: int = 300
    rate_limit_writes_per_minute: int = 60

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
            if self.bank_backend == "tbank" and not all(
                (
                    self.tbank_terminal_key,
                    self.tbank_password,
                    self.tbank_payout_terminal_key,
                    self.tbank_payout_password,
                )
            ):
                raise ValueError("Для Т-Банка нужны ключи обоих терминалов (TBANK_*)")
            if self.bank_webhook_secret == DEV_BANK_SECRET or len(self.bank_webhook_secret) < 32:
                raise ValueError("В prod нужен собственный BANK_WEBHOOK_SECRET от 32 символов")
            if not self.public_base_url.startswith("https://"):
                raise ValueError("В prod PUBLIC_BASE_URL должен начинаться с https://")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
