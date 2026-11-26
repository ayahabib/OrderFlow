"""FastAPI entry point."""

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from app.api import assistant, audit, channels, conversations, customers, escalations, interests, knowledge, me, notifications, offers, orders, owner_chat, products, reports, stores, tracking, zones
from app.config import get_settings
from app.webhooks import telegram as telegram_webhook
from app.webhooks import web_chat
from app.db.pool import create_pool
from app.engine.reply_queue import ReplyQueue
from app.utils.rate_limit import RateLimiter


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
    app.state.web_limiter = RateLimiter()  # per app, so each test app starts with clean counts
    app.state.reply_queue = ReplyQueue()   # groups quick split-up messages per conversation (task 3.10)
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

    # Every write on these routers is recorded in the audit log (task 3.14).
    owner_writes = [Depends(audit.record_owner_change)]
    app.include_router(me.router)
    app.include_router(stores.router)
    app.include_router(notifications.router, dependencies=owner_writes)
    app.include_router(products.router, dependencies=owner_writes)
    app.include_router(products.variants_router, dependencies=owner_writes)
    app.include_router(offers.router, dependencies=owner_writes)
    app.include_router(zones.router, dependencies=owner_writes)
    app.include_router(orders.router, dependencies=owner_writes)
    app.include_router(customers.router, dependencies=owner_writes)
    app.include_router(interests.router, dependencies=owner_writes)
    app.include_router(reports.router)
    app.include_router(owner_chat.router, dependencies=owner_writes)
    app.include_router(assistant.router, dependencies=owner_writes)
    app.include_router(tracking.router)
    app.include_router(audit.router)
    app.include_router(conversations.router, dependencies=owner_writes)
    app.include_router(escalations.router, dependencies=owner_writes)
    app.include_router(knowledge.router, dependencies=owner_writes)
    app.include_router(channels.router, dependencies=owner_writes)
    app.include_router(web_chat.router)
    app.include_router(telegram_webhook.router)
    return app


app = create_app()
