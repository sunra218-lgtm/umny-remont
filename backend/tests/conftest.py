import os

os.environ.setdefault(
    "TEST_DATABASE_URL", "postgresql+asyncpg://umny:umny@localhost:5432/umny_test"
)
os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
os.environ["ENV"] = "test"
os.environ["OTP_DEBUG"] = "true"
os.environ["BOOTSTRAP_ADMIN_PHONES"] = "+79990000001"
os.environ["RATE_LIMIT_PER_MINUTE"] = "0"

import uuid  # noqa: E402
from collections.abc import AsyncIterator  # noqa: E402

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fakeredis import FakeAsyncRedis  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.core.db import set_engine  # noqa: E402
from app.core.redis import set_redis  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Base  # noqa: E402

ADMIN_PHONE = "+79990000001"
DB_URL = os.environ["TEST_DATABASE_URL"]


@pytest.fixture(scope="session", autouse=True)
def migrated_db() -> None:
    """Чистая БД, накатанная той же миграцией, что и в продакшене."""
    import asyncio

    async def _reset() -> None:
        engine = create_async_engine(DB_URL, poolclass=NullPool)
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
        await engine.dispose()

    asyncio.run(_reset())
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option(
        "script_location", os.path.join(os.path.dirname(__file__), "..", "migrations")
    )
    cfg.attributes["database_url"] = DB_URL
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
async def engine():  # type: ignore[no-untyped-def]
    eng = create_async_engine(DB_URL, poolclass=NullPool)
    set_engine(eng)
    yield eng
    await eng.dispose()


@pytest.fixture(autouse=True)
async def clean(engine) -> AsyncIterator[None]:  # type: ignore[no-untyped-def]
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    set_redis(FakeAsyncRedis(decode_responses=True))
    yield


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def idem() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


async def login(client: AsyncClient, phone: str, *, marketing: bool = False) -> dict[str, str]:
    """Полный вход по СМС-коду. Возвращает заголовки с access-токеном."""
    from app.core.redis import get_redis

    # Сбрасываем паузу между отправками, чтобы в тестах можно было входить повторно.
    await get_redis().delete(f"otp:cool:{phone}")
    sent = await client.post("/api/v1/auth/otp", json={"phone": phone})
    assert sent.status_code == 202, sent.text
    code = sent.json()["debug_code"]
    resp = await client.post(
        "/api/v1/auth/otp/verify",
        json={
            "phone": phone,
            "code": code,
            "accept_pd_processing": True,
            "accept_marketing": marketing,
        },
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
async def admin(client: AsyncClient) -> dict[str, str]:
    return await login(client, ADMIN_PHONE)
