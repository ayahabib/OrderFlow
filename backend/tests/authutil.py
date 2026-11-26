"""Shared test helpers: sign test tokens and build an app wired to a given connection."""

import time
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import Depends

from app.api import auth
from app.api.auth import get_current_store_id
from app.config import Settings, get_settings
from app.db.pool import get_db, get_db_factory
from app.llm.client import get_llm
from app.llm.schemas import MessageAnalysis
from app.main import create_app
from app.llm.embeddings import get_embedder
from app.services.notifications import get_notifier

TEST_URL = "https://test-project.supabase.co"
TEST_ISSUER = f"{TEST_URL}/auth/v1"
HS_SECRET = "test-only-hs256-secret-at-least-32-bytes-long"
WEB_SECRET = "test-only-web-chat-secret"
EC_KEY = ec.generate_private_key(ec.SECP256R1())
OTHER_EC_KEY = ec.generate_private_key(ec.SECP256R1())


def test_settings(**overrides) -> Settings:
    values = dict(supabase_url=TEST_URL, database_url="postgresql://unused", supabase_jwt_secret=HS_SECRET,
                  web_chat_secret=WEB_SECRET,
                  chat_debounce_seconds=0,  # answer at once in tests (tests/test_reply_queue.py covers the pause)
                  # Simulator limits off unless a test sets them (tests/test_web_chat_limits.py does).
                  web_chat_max_messages_per_session=0, web_chat_max_messages_per_ip_hour=0,
                  web_chat_max_sessions_per_ip_hour=0, web_chat_max_messages_per_store_day=0)
    values.update(overrides)
    return Settings(**values)


def make_token(sub=None, *, key=EC_KEY, alg="ES256", exp_in=3600, aud="authenticated", iss=TEST_ISSUER, **extra):
    claims = {"sub": str(sub or uuid.uuid4()), "aud": aud, "iss": iss,
              "exp": int(time.time()) + exp_in, "role": "authenticated", **extra}
    return jwt.encode(claims, key, algorithm=alg, headers={"kid": "test-kid"})


def bearer(token) -> dict:
    return {"Authorization": f"Bearer {token}"}


def patch_jwks(monkeypatch) -> None:
    """JWKS lookups return the test EC public key instead of calling Supabase."""
    client = SimpleNamespace(get_signing_key_from_jwt=lambda token: SimpleNamespace(key=EC_KEY.public_key()))
    monkeypatch.setattr(auth, "_jwks_client", lambda url: client)


class ScriptedLLM:
    """Fake LLM: returns a scripted MessageAnalysis per exact customer text; records every call.

    Unscripted texts come back as intent "other" with nothing extracted. Never calls OpenAI.
    """

    def __init__(self, script: dict | None = None, answers: dict | None = None, edits: dict | None = None,
                 tools: dict | None = None):
        self.script = script or {}
        self.answers = answers or {}   # question text -> answer for the QA call (2.2); missing = NOT_FOUND
        self.edits = edits or {}       # edit message -> list of EditOp dicts (2.6); missing = no operations
        self.tools = tools or {}       # owner chat text -> tool arguments model (3.3); missing = no tool
        self.calls: list[dict] = []

    async def call_tool(self, *, system, user, tools, task):
        self.calls.append({"user": user, "task": task})
        text = user.rsplit("Owner message:\n<<<\n", 1)[1].rsplit("\n>>>", 1)[0]
        result = self.tools.get(text)
        if isinstance(result, Exception):
            raise result
        return result

    async def structured(self, *, system, user, schema, task):
        self.calls.append({"user": user, "task": task})
        if task == "extract_edits":
            from app.llm.edits import EditOp, OrderEdits
            text = user.rsplit("Customer message:\n<<<\n", 1)[1].rsplit("\n>>>", 1)[0]
            base = dict(line=None, quantity=None, color=None, size=None, product_text=None)
            return OrderEdits(ops=[EditOp(**{**base, **op}) for op in self.edits.get(text, [])])
        if task == "answer_question":
            from app.llm.qa import QAAnswer
            question = user.rsplit("Customer question:\n<<<\n", 1)[1].rsplit("\n>>>", 1)[0]
            answer = self.answers.get(question)
            if isinstance(answer, Exception):
                raise answer
            return QAAnswer(found=answer is not None, answer=answer)
        text = user.rsplit("Customer message:\n<<<\n", 1)[1].rsplit("\n>>>", 1)[0]
        result = self.script.get(text)
        if isinstance(result, Exception):
            raise result
        return result or analysis(intent="other")


def analysis(**kw) -> MessageAnalysis:
    base = dict(intent="order", sensitive_reason=None, items=[], address_text=None, phone=None, wants_to_confirm=False)
    return MessageAnalysis(**{**base, **kw})


def build_app(conn, settings: Settings | None = None, llm=None, notifier=None, embedder=None):
    app = create_app(use_lifespan=False)
    app.dependency_overrides[get_settings] = lambda: settings or test_settings()
    fake_llm = llm if llm is not None else ScriptedLLM()
    app.dependency_overrides[get_llm] = lambda: fake_llm
    # No real Telegram in tests: None (alerts off) unless a FakeNotifier is passed.
    app.dependency_overrides[get_notifier] = lambda: notifier
    fake_embedder = embedder if embedder is not None else FakeEmbedder()
    app.dependency_overrides[get_embedder] = lambda: fake_embedder

    async def override_db():
        yield conn

    app.dependency_overrides[get_db] = override_db

    # Background tasks borrow "another" connection: in tests it is the same rolled-back one.
    @asynccontextmanager
    async def same_conn():
        yield conn

    app.dependency_overrides[get_db_factory] = lambda: same_conn

    # A test-only route protected by get_current_store_id.
    @app.get("/api/_store_probe")
    async def probe(store_id: uuid.UUID = Depends(get_current_store_id)):
        return {"store_id": str(store_id)}

    return app


def client_for(conn, settings: Settings | None = None, llm=None, notifier=None, embedder=None) -> httpx.AsyncClient:
    app = build_app(conn, settings, llm, notifier, embedder)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


class FakeEmbedder:
    """Deterministic stand-in for OpenAI embeddings: hashed character trigrams of normalized
    Arabic, so texts sharing words land close together. Never calls OpenAI.
    """

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        import hashlib
        import math

        from app.llm.client import LLMError
        from app.utils.arabic import normalize_arabic
        self.calls.append(texts)
        if self.fail:
            raise LLMError("timeout", "embedding failed (timeout)")
        out = []
        for text in texts:
            vec = [0.0] * 768
            for word in normalize_arabic(text).split():
                word = f" {word} "
                for i in range(len(word) - 2):
                    vec[int(hashlib.md5(word[i:i + 3].encode()).hexdigest(), 16) % 768] += 1.0
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            out.append([x / norm for x in vec])
        return out


class FakeNotifier:
    """Stands in for TelegramNotifier: records sent messages, never calls Telegram."""

    link_secret = b"test-link-secret"

    def __init__(self, payloads: list[tuple[int, str]] | None = None, fail: bool = False, has_webhook: bool = False):
        self.sent: list[tuple[int, str]] = []
        self.payloads = payloads or []
        self.fail = fail
        self.has_webhook = has_webhook

    async def send(self, chat_id: int, text: str) -> None:
        from app.services.notifications import NotifyError
        if self.fail:
            raise NotifyError("telegram sendMessage failed: 403 Forbidden")
        self.sent.append((chat_id, text))

    async def bot_username(self) -> str:
        return "test_notify_bot"

    async def start_payloads(self) -> list[tuple[int, str]]:
        from app.services.notifications import NotifyError
        if self.has_webhook:
            raise NotifyError("telegram getUpdates failed: 409 Conflict", 409)
        return self.payloads


async def insert_auth_user(conn, user_id: uuid.UUID | None = None) -> uuid.UUID:
    """Insert a bare auth.users row (tests run inside a rolled-back transaction)."""
    user_id = user_id or uuid.uuid4()
    await conn.execute(
        "insert into auth.users (id, email, aud, role) values ($1, $2, 'authenticated', 'authenticated')",
        user_id, f"test-{user_id.hex[:8]}@example.test",
    )
    return user_id
