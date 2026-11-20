"""FastAPI entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from app.api import conversations, me, notifications, offers, orders, products, stores, zones
from app.config import get_settings
from app.webhooks import web_chat
from app.db.pool import create_pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db_pool = await create_pool(get_settings())
    try:
        yield
    finally:
        await app.state.db_pool.close()


def create_app(use_lifespan: bool = True) -> FastAPI:
    """Build the app. Tests pass use_lifespan=False and override get_db instead."""
    settings = get_settings()
    app = FastAPI(title="Store AI Backend", version="0.1.0", lifespan=lifespan if use_lifespan else None)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type", "X-Session-Token"],
    )

    @app.get("/health", response_class=PlainTextResponse)
    async def health() -> str:
        """Liveness check used by deploys and local smoke tests."""
        return "ok"

    app.include_router(me.router)
    app.include_router(stores.router)
    app.include_router(notifications.router)
    app.include_router(products.router)
    app.include_router(products.variants_router)
    app.include_router(offers.router)
    app.include_router(zones.router)
    app.include_router(orders.router)
    app.include_router(conversations.router)
    app.include_router(web_chat.router)
    return app


app = create_app()
