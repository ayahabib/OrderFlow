"""FastAPI entry point."""

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

app = FastAPI(title="Store AI Backend", version="0.1.0")


@app.get("/health", response_class=PlainTextResponse)
async def health() -> str:
    """Liveness check used by deploys and local smoke tests."""
    return "ok"
