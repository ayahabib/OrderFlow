"""Knowledge base (TASKS.md 2.1). Fake embeddings only: no OpenAI in tests (CLAUDE.md)."""

from types import SimpleNamespace

import pytest

from app.db.knowledge import vector_literal
from app.llm.client import LLMError
from app.llm.embeddings import OpenAIEmbedder
from app.services.knowledge import KnowledgeFileError, decode_text_file, embedding_text, split_paragraphs
from tests.authutil import FakeEmbedder, bearer, client_for, insert_auth_user, make_token, patch_jwks
from tests.authutil import test_settings as make_settings


# --- pure parts -------------------------------------------------------------------------

def test_split_paragraphs():
    text = "الدوام من 10 لـ 8\r\nكل يوم\n\n\n  التوصيل خلال يومين  \n\n"
    assert split_paragraphs(text) == ["الدوام من 10 لـ 8 كل يوم", "التوصيل خلال يومين"]
    with pytest.raises(KnowledgeFileError):
        split_paragraphs(" \n\n ")
    with pytest.raises(KnowledgeFileError, match="قسّمها"):
        split_paragraphs("ا" * 2001)
    with pytest.raises(KnowledgeFileError):
        split_paragraphs("\n\n".join(["فقرة"] * 201))


def test_decode_text_file():
    assert decode_text_file("مرحبا".encode("utf-8-sig")) == "مرحبا"
    assert decode_text_file("مرحبا".encode("cp1256")) == "مرحبا"
    with pytest.raises(KnowledgeFileError):
        decode_text_file(b"%PDF-1.4\x00\x01binary")


def test_embedding_text_and_vector_literal():
    assert embedding_text("شو الدوام؟", "من 10 لـ 8") == "شو الدوام؟\nمن 10 لـ 8"
    assert embedding_text(None, "من 10 لـ 8") == "من 10 لـ 8"
    assert vector_literal([0.5, -0.25, 1e-9]) == "[0.5,-0.25,1e-09]"


class _FakeOpenAI:
    def __init__(self, results):
        self.results, self.calls = list(results), []
        self.embeddings = SimpleNamespace(create=self.create)

    async def create(self, **kw):
        self.calls.append(kw)
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return SimpleNamespace(data=[SimpleNamespace(index=i, embedding=v) for i, v in enumerate(r)])


async def test_openai_embedder_asks_for_768_dims_and_retries_once():
    import openai
    timeout = openai.APITimeoutError(request=None)
    client = _FakeOpenAI([timeout, [[0.1] * 768, [0.2] * 768]])
    embedder = OpenAIEmbedder(make_settings(), client=client, retry_delay_s=0)
    vectors = await embedder.embed(["أ", "ب"])
    assert [v[0] for v in vectors] == [0.1, 0.2]
    assert len(client.calls) == 2 and client.calls[0]["dimensions"] == 768

    with pytest.raises(LLMError):  # two timeouts -> give up
        await OpenAIEmbedder(make_settings(), client=_FakeOpenAI([timeout, timeout]), retry_delay_s=0).embed(["أ"])
    with pytest.raises(LLMError, match="shape"):
        await OpenAIEmbedder(make_settings(), client=_FakeOpenAI([[[0.1] * 10]]), retry_delay_s=0).embed(["أ"])


# --- API (database) ----------------------------------------------------------------------

@pytest.fixture
def fake_jwks(monkeypatch):
    patch_jwks(monkeypatch)


async def owner(conn):
    user = await insert_auth_user(conn)
    store = await conn.fetchval("insert into stores (owner_id, name) values ($1, 'متجر') returning id", user)
    return bearer(make_token(user)), store


@pytest.mark.db
async def test_done_when_add_info_and_find_it_with_other_wording(db_tx, fake_jwks):
    """Done-when: add info, then search with a differently worded question and get it back first."""
    h, store = await owner(db_tx)
    async with client_for(db_tx) as c:
        r = await c.post("/api/knowledge", headers=h, json={"question": "شو أوقات الدوام؟",
                                                             "answer": "منفتح من 10 الصبح لـ 8 المسا، ما عدا الجمعة."})
        assert r.status_code == 201 and r.json()["source"] == "manual"
        await c.post("/api/knowledge", headers=h, json={"answer": "التوصيل لنابلس ورام الله خلال يومين."})
        await c.post("/api/knowledge", headers=h, json={"answer": "الإرجاع خلال 3 أيام إذا القطعة ما انلبست."})

        hits = (await c.post("/api/knowledge/search", headers=h, json={"query": "لإيمتى الدوام عندكم؟"})).json()
        assert hits[0]["question"] == "شو أوقات الدوام؟"
        assert hits[0]["similarity"] > hits[1]["similarity"]

        listed = (await c.get("/api/knowledge", headers=h)).json()
        assert len(listed) == 3
    assert await db_tx.fetchval("select count(*) from knowledge_base where store_id = $1 and embedding is not null", store) == 3


@pytest.mark.db
async def test_upload_edit_disable_delete(db_tx, fake_jwks):
    h, store = await owner(db_tx)
    embedder = FakeEmbedder()
    async with client_for(db_tx, embedder=embedder) as c:
        files = {"file": ("info.txt", "الدوام من 10 لـ 8\n\nالتوصيل خلال يومين".encode(), "text/plain")}
        r = await c.post("/api/knowledge/upload", headers=h, files=files)
        assert r.status_code == 201 and [e["source"] for e in r.json()] == ["upload", "upload"]
        assert embedder.calls[-1] == ["الدوام من 10 لـ 8", "التوصيل خلال يومين"]   # one batch
        entry = r.json()[0]["id"]

        calls = len(embedder.calls)
        r = await c.patch(f"/api/knowledge/{entry}", headers=h, json={"is_active": False})
        assert r.json()["is_active"] is False and len(embedder.calls) == calls    # no new embedding
        hits = (await c.post("/api/knowledge/search", headers=h, json={"query": "الدوام"})).json()
        assert entry not in [x["id"] for x in hits]                               # inactive: not searched

        r = await c.patch(f"/api/knowledge/{entry}", headers=h, json={"answer": "الدوام من 9 لـ 9"})
        assert r.json()["answer"] == "الدوام من 9 لـ 9" and len(embedder.calls) == calls + 2  # re-embedded

        assert (await c.delete(f"/api/knowledge/{entry}", headers=h)).status_code == 204
        assert (await c.delete(f"/api/knowledge/{entry}", headers=h)).status_code == 404

        bad = {"file": ("info.pdf", b"%PDF", "application/pdf")}
        assert (await c.post("/api/knowledge/upload", headers=h, files=bad)).status_code == 415


@pytest.mark.db
async def test_embedding_failure_saves_nothing(db_tx, fake_jwks):
    h, store = await owner(db_tx)
    async with client_for(db_tx, embedder=FakeEmbedder(fail=True)) as c:
        r = await c.post("/api/knowledge", headers=h, json={"answer": "معلومة"})
    assert r.status_code == 502 and "ما انحفظ" in r.json()["detail"]
    assert await db_tx.fetchval("select count(*) from knowledge_base where store_id = $1", store) == 0


@pytest.mark.db
async def test_stores_never_see_each_others_knowledge(db_tx, fake_jwks):
    h_a, _ = await owner(db_tx)
    h_b, _ = await owner(db_tx)
    async with client_for(db_tx) as c:
        entry = (await c.post("/api/knowledge", headers=h_a, json={"answer": "الدوام من 10 لـ 8"})).json()["id"]
        assert (await c.get("/api/knowledge", headers=h_b)).json() == []
        assert (await c.post("/api/knowledge/search", headers=h_b, json={"query": "الدوام"})).json() == []
        assert (await c.patch(f"/api/knowledge/{entry}", headers=h_b, json={"is_active": False})).status_code == 404
        assert (await c.delete(f"/api/knowledge/{entry}", headers=h_b)).status_code == 404
        r = await c.post("/api/knowledge", headers=h_b, json={"answer": "x", "store_id": "anything"})
        assert r.status_code == 422                                              # no store_id in the body
