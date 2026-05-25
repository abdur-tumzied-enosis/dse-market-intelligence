# api/main.py
from __future__ import annotations
from contextlib import asynccontextmanager
import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.config import get_settings
from api.middleware.rate_limit import RateLimitMiddleware
from api.auth.router import router as auth_router
from api.routers.stocks import router as stocks_router
from api.routers.market import router as market_router
from api.routers.sectors import router as sectors_router
from api.routers.analyze import router as analyze_router
from db.pool import close_pool, get_pool
from mgmt.cache import close_redis, get_redis

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await get_pool()
    await get_redis()
    logger.info("api_startup_complete")
    yield
    await close_pool()
    await close_redis()
    logger.info("api_shutdown_complete")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="DSE Stock API", version="1.0.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.api_cors_origins.split(",")],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RateLimitMiddleware)

    app.include_router(auth_router, prefix="/api")
    app.include_router(stocks_router, prefix="/api")
    app.include_router(market_router, prefix="/api")
    app.include_router(sectors_router, prefix="/api")
    app.include_router(analyze_router, prefix="/api")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


app = create_app()
