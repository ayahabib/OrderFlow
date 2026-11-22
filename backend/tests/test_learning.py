"""Learning from the owner's answers (TASKS.md 2.4).

Done-when: after "save for the bot", the same question in other words is answered without
escalation; a "private" answer is never stored. Fake embeddings and a scripted LLM only.
"""

import pytest

from tests.authutil import FakeEmbedder, ScriptedLLM, analysis, bearer, client_for, make_token, patch_jwks
from tests.test_order_flow import Chat, escalations, new_store

pytestmark = pytest.mark.db

ASKED = "بتعملوا تفصيل على المقاس؟"
ASKED_AGAIN = "في عندكم تفصيل حسب المقاس تبعي؟"
OWNER_ANSWER = "اه منعمل تفصيل، بياخد أسبوعين تقريباً."


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch):
    patch_jwks(monkeypatch)


async def owner_headers(conn, store):
    return bearer(make_token(await conn.fetchval("select owner_id from stores where id = $1", store)))


def scripted():
    return ScriptedLLM({ASKED: analysis(intent="question"), ASKED_AGAIN: analysis(intent="question")},
                       answers={ASKED_AGAIN: OWNER_ANSWER})


async def open_escalation(c, h):
    [esc] = [e for e in (await c.get("/api/escalations", headers=h)).json()]
    return esc


async def test_done_when_saved_answer_is_learned(db_tx):
    store = await new_store(db_tx)
    h = await owner_headers(db_tx, store)
    embedder = FakeEmbedder()
    async with client_for(db_tx, llm=scripted(), embedder=embedder) as c:
        await Chat(c, store).say(ASKED)                                  # nothing in the KB -> owner
        esc = await open_escalation(c, h)
        assert esc["rule"] == "not_in_data"

        r = await c.post(f"/api/escalations/{esc['id']}/answer", headers=h, json={
            "answer": "اه حبيبتي منعمل، بياخد أسبوعين تقريباً.", "save_to_kb": True, "then": "bot",
            "kb_question": "بتعملوا تفصيل حسب المقاس؟", "kb_answer": OWNER_ANSWER,     # reworded for the bot
        })
        assert r.status_code == 200

        kb = await db_tx.fetchrow(
            "select question, answer, source::text as source, escalation_id, embedding is not null as embedded "
            "from knowledge_base where store_id = $1", store)
        assert (kb["question"], kb["answer"], kb["source"], str(kb["escalation_id"]), kb["embedded"]) == \
            ("بتعملوا تفصيل حسب المقاس؟", OWNER_ANSWER, "owner_answer", esc["id"], True)

        # Another customer, other words: answered by the bot, no new escalation.
        reply = await Chat(c, store).say(ASKED_AGAIN)
        assert reply["sender"] == "bot" and reply["content"] == OWNER_ANSWER
    assert len(await escalations(db_tx, store)) == 1


async def test_private_answer_is_never_stored(db_tx):
    store = await new_store(db_tx)
    h = await owner_headers(db_tx, store)
    async with client_for(db_tx, llm=scripted()) as c:
        await Chat(c, store).say(ASKED)
        esc = await open_escalation(c, h)
        # Rewording is only for answers saved for the bot.
        r = await c.post(f"/api/escalations/{esc['id']}/answer", headers=h,
                         json={"answer": OWNER_ANSWER, "save_to_kb": False, "kb_answer": "x"})
        assert r.status_code == 422
        r = await c.post(f"/api/escalations/{esc['id']}/answer", headers=h,
                         json={"answer": OWNER_ANSWER, "save_to_kb": False})
        assert r.status_code == 200
        await Chat(c, store).say(ASKED_AGAIN)                              # still not known -> owner again
    assert await db_tx.fetchval("select count(*) from knowledge_base where store_id = $1", store) == 0
    assert len(await escalations(db_tx, store)) == 2


async def test_forced_rules_and_embedding_failure_save_and_send_nothing(db_tx):
    store = await new_store(db_tx)
    h = await owner_headers(db_tx, store)
    async with client_for(db_tx, llm=scripted(), embedder=FakeEmbedder(fail=True)) as c:
        await Chat(c, store).say("بدي احكي مع حدا")                       # forced rule, can't be learned
        human = await open_escalation(c, h)
        r = await c.post(f"/api/escalations/{human['id']}/answer", headers=h,
                         json={"answer": "هلا", "save_to_kb": True})
        assert r.status_code == 422

        await Chat(c, store).say(ASKED)
        # The failing embedder already broke the bot's search, so this one escalated as llm_failure (learnable).
        [question] = [e for e in (await c.get("/api/escalations", headers=h)).json() if e["rule"] == "llm_failure"]
        r = await c.post(f"/api/escalations/{question['id']}/answer", headers=h,
                         json={"answer": OWNER_ANSWER, "save_to_kb": True})
        assert r.status_code == 502                                         # embedding failed
    assert await db_tx.fetchval("select count(*) from messages where store_id = $1 and sender = 'owner'", store) == 0
    assert await db_tx.fetchval("select count(*) from escalations where store_id = $1 and status = 'open'", store) == 2
    assert await db_tx.fetchval("select count(*) from knowledge_base where store_id = $1", store) == 0
