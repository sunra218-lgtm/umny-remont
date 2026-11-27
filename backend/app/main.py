import logging

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import get_engine
from app.core.idempotency import IdempotencyMiddleware
from app.core.problems import install_problem_handlers
from app.core.redis import get_redis
from app.modules.admin.router import router as admin_router
from app.modules.auth.router import router as auth_router
from app.modules.me.router import router as me_router
from app.modules.orgs.router import router as orgs_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Умный ремонт API",
        version="0.1.0",
        description="API платформы «Умный ремонт». Суммы — целые копейки (RUB).",
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/docs",
        redoc_url=None,
    )
    install_problem_handlers(app)
    app.add_middleware(IdempotencyMiddleware)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    api = APIRouter(prefix="/api/v1")
    api.include_router(auth_router)
    api.include_router(me_router)
    api.include_router(orgs_router)
    api.include_router(admin_router)

    @api.get("/health", tags=["system"], summary="Проверка работоспособности")
    async def health() -> dict[str, str]:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        await get_redis().ping()
        return {"status": "ok"}

    app.include_router(api)
    return app


app = create_app()
