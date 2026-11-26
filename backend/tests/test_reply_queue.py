"""Split-up messages answered together (task 3.10). No DB: the reply is a fake."""

import asyncio
import uuid

from app.engine.reply_queue import ReplyQueue

PAUSE = 0.05


class Recorder:
    def __init__(self, hold: asyncio.Event | None = None):
        self.calls: list[str] = []
        self.hold = hold  # when set, each reply waits for it (a slow LLM call)

    async def __call__(self, text: str) -> None:
        self.calls.append(text)
        if self.hold:
            await self.hold.wait()


async def send(queue, conv, text, reply, delay=PAUSE):
    """What a webhook does: buffer the text, start the background run."""
    ticket = queue.add(conv, text)
    return asyncio.create_task(queue.run(conv, ticket, delay, reply))


async def test_three_quick_messages_get_one_reply():
    """Done-when: three messages in a row are read as one, with one reply."""
    queue, conv, reply = ReplyQueue(), uuid.uuid4(), Recorder()
    tasks = [await send(queue, conv, t, reply) for t in ["بدي", "فستان أسود", "مقاس M"]]
    await asyncio.gather(*tasks)
    assert reply.calls == ["بدي\nفستان أسود\nمقاس M"]


async def test_messages_far_apart_get_separate_replies():
    queue, conv, reply = ReplyQueue(), uuid.uuid4(), Recorder()
    await (await send(queue, conv, "مرحبا", reply))
    await (await send(queue, conv, "شو عندكم؟", reply))
    assert reply.calls == ["مرحبا", "شو عندكم؟"]


async def test_conversations_do_not_mix():
    queue, reply_a, reply_b = ReplyQueue(), Recorder(), Recorder()
    a, b = uuid.uuid4(), uuid.uuid4()
    tasks = [await send(queue, a, "بدي قميص", reply_a), await send(queue, b, "بدي بنطلون", reply_b)]
    await asyncio.gather(*tasks)
    assert (reply_a.calls, reply_b.calls) == (["بدي قميص"], ["بدي بنطلون"])


async def test_message_during_a_running_reply_waits_for_the_next_turn():
    """Never two replies at once for one conversation; nothing is dropped."""
    hold = asyncio.Event()
    queue, conv, reply = ReplyQueue(), uuid.uuid4(), Recorder(hold)
    first = await send(queue, conv, "بدي فستان", reply)
    await asyncio.sleep(PAUSE * 3)               # the first reply is now running (held)
    assert reply.calls == ["بدي فستان"]
    second = await send(queue, conv, "أحمر", reply)
    third = await send(queue, conv, "مقاس M", reply)
    await asyncio.sleep(PAUSE * 3)
    assert reply.calls == ["بدي فستان"]          # still one: the lock holds the next turn
    hold.set()
    await asyncio.gather(first, second, third)
    assert reply.calls == ["بدي فستان", "أحمر\nمقاس M"]


async def test_zero_delay_answers_each_message_at_once():
    queue, conv, reply = ReplyQueue(), uuid.uuid4(), Recorder()
    await (await send(queue, conv, "تأكيد", reply, delay=0))
    assert reply.calls == ["تأكيد"]


async def test_memory_is_released_after_replying():
    queue, conv, reply = ReplyQueue(), uuid.uuid4(), Recorder()
    await asyncio.gather(*[await send(queue, conv, t, reply) for t in ["a", "b"]])
    assert queue.pending_count() == 0
    assert not queue._ticket and not queue._locks


async def test_a_failed_reply_does_not_block_later_messages():
    queue, conv = ReplyQueue(), uuid.uuid4()
    calls = []

    async def flaky(text):
        calls.append(text)
        if len(calls) == 1:
            raise RuntimeError("LLM down")

    first = await send(queue, conv, "مرحبا", flaky)
    try:
        await first
    except RuntimeError:
        pass
    await (await send(queue, conv, "مرحبا مرة ثانية", flaky))
    assert calls == ["مرحبا", "مرحبا مرة ثانية"]
