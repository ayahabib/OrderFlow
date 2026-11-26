"""In-memory sliding-window rate limiter (one server process, so no shared store needed).

Used by the public web chat simulator, where every message costs an LLM call.
Counts are lost on restart, which is fine for a cost guard.
"""

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

# Drop idle keys once the table grows past this, so memory stays bounded.
_SWEEP_AT = 10_000


@dataclass(frozen=True)
class Rule:
    key: str        # e.g. "ip-msg:1.2.3.4"
    limit: int      # max hits inside the window; 0 or less = no limit
    window: float   # seconds


class RateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}

    def _recent(self, rule: Rule, now: float) -> deque[float]:
        hits = self._hits.setdefault(rule.key, deque())
        while hits and hits[0] <= now - rule.window:
            hits.popleft()
        return hits

    def exceeded(self, rules: list[Rule]) -> Rule | None:
        """The first rule that is already at its limit, or None. Records nothing."""
        now = self._clock()
        for rule in rules:
            if rule.limit > 0 and len(self._recent(rule, now)) >= rule.limit:
                return rule
        return None

    def record(self, rules: list[Rule]) -> None:
        now = self._clock()
        for rule in rules:
            self._recent(rule, now).append(now)
        if len(self._hits) > _SWEEP_AT:
            self._sweep(now, max(r.window for r in rules))

    def _sweep(self, now: float, window: float) -> None:
        for key in [k for k, hits in self._hits.items() if not hits or hits[-1] <= now - window]:
            del self._hits[key]
