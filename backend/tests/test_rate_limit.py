"""In-memory sliding-window limiter (task 3.8). No DB."""

from app.utils.rate_limit import RateLimiter, Rule


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def hit(limiter, rules):
    """Check then record, as the endpoints do. True when allowed."""
    if limiter.exceeded(rules):
        return False
    limiter.record(rules)
    return True


def test_allows_up_to_the_limit_then_refuses():
    limiter = RateLimiter(Clock())
    rules = [Rule("a", 3, 60)]
    assert [hit(limiter, rules) for _ in range(4)] == [True, True, True, False]


def test_window_slides():
    clock = Clock()
    limiter = RateLimiter(clock)
    rules = [Rule("a", 2, 60)]
    assert hit(limiter, rules) and hit(limiter, rules)
    assert not hit(limiter, rules)
    clock.now += 59
    assert not hit(limiter, rules)
    clock.now += 2  # the first two hits are now older than 60 s
    assert hit(limiter, rules)


def test_keys_are_separate():
    limiter = RateLimiter(Clock())
    assert hit(limiter, [Rule("ip:1", 1, 60)])
    assert not hit(limiter, [Rule("ip:1", 1, 60)])
    assert hit(limiter, [Rule("ip:2", 1, 60)])


def test_first_exceeded_rule_is_returned_and_nothing_recorded():
    limiter = RateLimiter(Clock())
    session, ip = Rule("session:x", 1, 60), Rule("ip:1", 5, 60)
    assert hit(limiter, [session, ip])
    assert limiter.exceeded([session, ip]) == session
    # A refused request records nothing, so the IP count is still 1.
    assert hit(limiter, [Rule("session:y", 1, 60), ip])
    assert limiter.exceeded([Rule("ip:1", 2, 60)]) == Rule("ip:1", 2, 60)  # exactly 2 recorded


def test_zero_limit_means_unlimited():
    limiter = RateLimiter(Clock())
    assert all(hit(limiter, [Rule("a", 0, 60)]) for _ in range(100))


def test_idle_keys_are_swept(monkeypatch):
    import app.utils.rate_limit as rl
    monkeypatch.setattr(rl, "_SWEEP_AT", 3)
    clock = Clock()
    limiter = RateLimiter(clock)
    for i in range(3):
        hit(limiter, [Rule(f"k{i}", 5, 60)])
    clock.now += 120
    hit(limiter, [Rule("k3", 5, 60)])  # 4 keys > 3 -> the three idle ones are dropped
    assert list(limiter._hits) == ["k3"]
