import logging

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import get_engine
from app.core.idempotency import IdempotencyMiddleware
from app.core.problems import install_problem_handlers
from app.core.protection import RateLimitMiddleware, SecurityHeadersMiddleware
from app.core.redis import get_redis
from app.modules.acts.router import router as acts_router
from app.modules.admin.kpi import router as kpi_router
from app.modules.admin.router import router as admin_router
from app.modules.auth.links import router as links_router
from app.modules.auth.router import router as auth_router
from app.modules.cameras.router import router as cameras_router
from app.modules.catalog.router import router as catalog_router
from app.modules.checklists.router import router as checklists_router
from app.modules.daily.router import router as daily_router
from app.modules.disputes.router import router as disputes_router
from app.modules.estimates.router import router as estimates_router
from app.modules.field.router import router as field_router
from app.modules.guide.router import router as guide_router
from app.modules.inspections.router import router as inspections_router
from app.modules.market.router import router as market_router
from app.modules.me.router import router as me_router
from app.modules.objects.router import router as objects_router
from app.modules.orgs.router import router as orgs_router
from app.modules.payments.router import router as payments_router
from app.modules.procurement.router import router as procurement_router
from app.modules.services.router import router as services_router
from app.modules.techcards.router import router as techcards_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Умный ремонт API",
        version="0.1.0",
        description="API платформы «Умный ремонт». Суммы — целые копейки (RUB).",
        # Описание API на сервере пилота не публикуется: меньше подсказок для атакующего.
        openapi_url=None if settings.env == "prod" else "/api/v1/openapi.json",
        docs_url=None if settings.env == "prod" else "/api/docs",
        redoc_url=None,
    )
    install_problem_handlers(app)
    app.add_middleware(IdempotencyMiddleware)
    # Добавленный последним выполняется первым: лимит отсекает лишнее до всей остальной работы.
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
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
    api.include_router(links_router)
    api.include_router(me_router)
    api.include_router(orgs_router)
    api.include_router(admin_router)
    api.include_router(kpi_router)
    api.include_router(objects_router)
    api.include_router(services_router)
    api.include_router(estimates_router)
    api.include_router(catalog_router)
    api.include_router(field_router)
    api.include_router(checklists_router)
    api.include_router(techcards_router)
    api.include_router(daily_router)
    api.include_router(guide_router)
    api.include_router(cameras_router)
    api.include_router(market_router)
    api.include_router(inspections_router)
    api.include_router(payments_router)
    api.include_router(acts_router)
    api.include_router(disputes_router)
    api.include_router(procurement_router)

    @api.get("/health", tags=["system"], summary="Проверка работоспособности")
    async def health() -> dict[str, str]:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        await get_redis().ping()
        return {"status": "ok"}

    app.include_router(api)
    return app


app = create_app()
