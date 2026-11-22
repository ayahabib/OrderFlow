"""Text embeddings for the knowledge base (ARCHITECTURE.md section 6).

OpenAI `text-embedding-3-small` asked for 768 dimensions, matching knowledge_base.embedding
vector(768). Same failure policy as chat calls: one retry on transient errors, then LLMError.
Logs counts and latency only, never the text.
"""

import asyncio
import logging
import time
from typing import Protocol

import openai

from app.config import Settings
from app.llm.client import DEFAULT_TIMEOUT_S, RETRY_DELAY_S, LLMError, _classify

log = logging.getLogger(__name__)

DIMENSIONS = 768
MAX_BATCH = 100  # inputs per API call


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAIEmbedder:
    def __init__(self, settings: Settings, *, client: openai.AsyncOpenAI | None = None,
                 timeout_s: float = DEFAULT_TIMEOUT_S, retry_delay_s: float = RETRY_DELAY_S):
        if client is None and settings.openai_api_key is None:
            raise LLMError("not_configured", "OPENAI_API_KEY is not set")
        self.model = settings.openai_embed_model
        self.timeout_s = timeout_s
        self.retry_delay_s = retry_delay_s
        self.client = client or openai.AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value(), max_retries=0)

    async def _batch(self, texts: list[str]) -> list[list[float]]:
        for attempt in (1, 2):
            started = time.perf_counter()
            try:
                result = await self.client.embeddings.create(
                    model=self.model, input=texts, dimensions=DIMENSIONS, timeout=self.timeout_s)
            except Exception as exc:
                kind, retryable = _classify(exc)
                log.info("embed model=%s n=%d attempt=%d ms=%d outcome=error:%s", self.model, len(texts), attempt,
                         (time.perf_counter() - started) * 1000, kind)
                if retryable and attempt == 1:
                    await asyncio.sleep(self.retry_delay_s)
                    continue
                raise LLMError(kind, f"embedding failed ({kind})") from exc
            vectors = [d.embedding for d in sorted(result.data, key=lambda d: d.index)]
            if len(vectors) != len(texts) or any(len(v) != DIMENSIONS for v in vectors):
                raise LLMError("invalid_output", "embedding has the wrong shape")
            log.info("embed model=%s n=%d attempt=%d ms=%d outcome=ok", self.model, len(texts), attempt,
                     (time.perf_counter() - started) * 1000)
            return vectors
        raise AssertionError("unreachable")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for i in range(0, len(texts), MAX_BATCH):
            vectors.extend(await self._batch(texts[i:i + MAX_BATCH]))
        return vectors


_cached: dict[tuple, OpenAIEmbedder] = {}


def get_embedder() -> Embedder | None:
    """FastAPI dependency. None when OpenAI isn't configured (the API then says so)."""
    from app.config import get_settings

    settings = get_settings()
    key = (settings.openai_embed_model, settings.openai_api_key.get_secret_value() if settings.openai_api_key else None)
    if key not in _cached:
        try:
            _cached[key] = OpenAIEmbedder(settings)
        except LLMError:
            return None
    return _cached[key]
