"""The only place that talks to OpenAI (CLAUDE.md conventions).

- Structured output parsed into a Pydantic model; anything else is an error.
- Hard rule 6: one retry on 429 / timeout / connection / 5xx / malformed output,
  then a typed LLMError so the caller escalates instead of losing the message.
- Logs model, latency, tokens and outcome only: never the customer's text.
"""

import asyncio
import logging
import time
from typing import Literal, Protocol, TypeVar

import openai
from pydantic import BaseModel, ValidationError

from app.config import Settings

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

ErrorKind = Literal["not_configured", "rate_limit", "timeout", "unavailable", "invalid_output", "refused", "api"]

DEFAULT_TIMEOUT_S = 20.0
RETRY_DELAY_S = 1.0


class LLMError(Exception):
    """The LLM gave no usable answer. `kind` says why; the message is safe to log."""

    def __init__(self, kind: ErrorKind, message: str):
        super().__init__(message)
        self.kind = kind


class LLM(Protocol):
    """What the rest of the app depends on. Tests pass a fake with the same method."""

    async def structured(self, *, system: str, user: str, schema: type[T], task: str) -> T: ...

    async def call_tool(self, *, system: str, user: str, tools: dict[str, type[BaseModel]],
                        task: str) -> BaseModel | None: ...


class _NoParsedOutput(Exception):
    """The response had no parsed object (empty or unparseable content)."""


# Errors worth one more try, and how we report them.
_RETRYABLE: tuple[tuple[type[Exception], ErrorKind], ...] = (
    (openai.RateLimitError, "rate_limit"),
    (openai.APITimeoutError, "timeout"),
    (openai.APIConnectionError, "unavailable"),
    (openai.InternalServerError, "unavailable"),
    (openai.LengthFinishReasonError, "invalid_output"),
    (ValidationError, "invalid_output"),
    (_NoParsedOutput, "invalid_output"),
)


def _classify(exc: Exception) -> tuple[ErrorKind, bool]:
    for cls, kind in _RETRYABLE:
        if isinstance(exc, cls):
            return kind, True
    if isinstance(exc, openai.ContentFilterFinishReasonError):
        return "refused", False
    return "api", False  # auth, bad request, ...: retrying won't help


class OpenAILLM:
    def __init__(self, settings: Settings, *, client: openai.AsyncOpenAI | None = None,
                 timeout_s: float = DEFAULT_TIMEOUT_S, retry_delay_s: float = RETRY_DELAY_S):
        # An empty LLM_API_KEY= / LLM_BASE_URL= line in .env means "not set".
        api_key = settings.llm_api_key if settings.llm_api_key and settings.llm_api_key.get_secret_value() \
            else settings.openai_api_key
        if client is None and (api_key is None or not settings.openai_model):
            raise LLMError("not_configured", "OPENAI_API_KEY / OPENAI_MODEL are not set")
        self.model = settings.openai_model or "test-model"
        self.timeout_s = timeout_s
        self.retry_delay_s = retry_delay_s
        # max_retries=0: retries are ours (exactly one), so the total wait stays predictable.
        self.client = client or openai.AsyncOpenAI(
            api_key=api_key.get_secret_value(), base_url=settings.llm_base_url or None, max_retries=0
        )

    async def _call_once(self, system: str, user: str, schema: type[T]) -> tuple[T, object]:
        completion = await self.client.chat.completions.parse(
            model=self.model,
            messages=[
                # Instructions and customer text are separate messages (hard rule 10).
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format=schema,
            timeout=self.timeout_s,
        )
        message = completion.choices[0].message
        if message.refusal:
            raise LLMError("refused", "the model refused to answer")
        if message.parsed is None:
            raise _NoParsedOutput()
        # Re-validate: never trust the shape of model output.
        return schema.model_validate(message.parsed.model_dump()), completion.usage

    async def structured(self, *, system: str, user: str, schema: type[T], task: str) -> T:
        return await self._with_retry(task, lambda: self._call_once(system, user, schema))

    async def _tool_once(self, system: str, user: str, tools: dict[str, type[BaseModel]]):
        completion = await self.client.chat.completions.parse(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            tools=[openai.pydantic_function_tool(model, name=name) for name, model in tools.items()],
            tool_choice="auto",
            timeout=self.timeout_s,
        )
        message = completion.choices[0].message
        if message.refusal:
            raise LLMError("refused", "the model refused to answer")
        calls = message.tool_calls or []
        if not calls:
            return None, completion.usage  # no tool fits: the caller shows a fixed help text
        call = calls[0]
        schema = tools.get(call.function.name)
        if schema is None:
            raise _NoParsedOutput()
        # Validate the arguments ourselves: never trust the shape of model output.
        return schema.model_validate_json(call.function.arguments), completion.usage

    async def call_tool(self, *, system: str, user: str, tools: dict[str, type[BaseModel]],
                        task: str) -> BaseModel | None:
        """Function calling: the arguments of the first tool the model picked, or None if it picked none.

        The model's free text is ignored on purpose; replies are built by code.
        """
        return await self._with_retry(task, lambda: self._tool_once(system, user, tools))

    async def _with_retry(self, task: str, call):
        for attempt in (1, 2):
            started = time.perf_counter()
            try:
                result, usage = await call()
            except LLMError as exc:
                self._log(task, attempt, started, f"error:{exc.kind}")
                raise
            except Exception as exc:
                kind, retryable = _classify(exc)
                self._log(task, attempt, started, f"error:{kind}")
                if retryable and attempt == 1:
                    await asyncio.sleep(self.retry_delay_s)
                    continue
                raise LLMError(kind, f"LLM call failed ({kind})") from exc
            self._log(task, attempt, started, "ok", usage)
            return result
        raise AssertionError("unreachable")

    def _log(self, task: str, attempt: int, started: float, outcome: str, usage=None) -> None:
        tokens = f" in={usage.prompt_tokens} out={usage.completion_tokens}" if usage else ""
        log.info("llm task=%s model=%s attempt=%d ms=%d outcome=%s%s",
                 task, self.model, attempt, (time.perf_counter() - started) * 1000, outcome, tokens)


_cached: dict[tuple, OpenAILLM] = {}


def get_llm() -> LLM | None:
    """FastAPI dependency. None when OpenAI isn't configured: the engine then escalates (hard rule 6).

    One client per (model, key), reused across requests.
    """
    from app.config import get_settings

    settings = get_settings()
    key = (settings.openai_model, settings.openai_api_key.get_secret_value() if settings.openai_api_key else None)
    if key not in _cached:
        try:
            _cached[key] = OpenAILLM(settings, timeout_s=settings.llm_timeout_seconds)
        except LLMError:
            return None
    return _cached[key]
