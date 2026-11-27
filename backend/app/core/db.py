from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        s = get_settings()
        _engine = create_async_engine(
            s.database_url,
            pool_pre_ping=True,
            pool_size=s.db_pool_size,
            max_overflow=s.db_max_overflow,
            pool_timeout=10,
        )
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def set_engine(engine: AsyncEngine) -> None:
    """Используется в тестах, чтобы подставить свой движок."""
    global _engine, _sessionmaker
    _engine = engine
    _sessionmaker = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    get_engine()
    assert _sessionmaker is not None
    async with _sessionmaker() as session:
        yield session
