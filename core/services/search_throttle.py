import asyncio
import contextlib
import os
import random
import time
from collections.abc import Callable
from typing import Any, Optional

# Called while a search is held back: (state, wait_until_epoch, reason).
# state is "waiting" (normal pacing between searches) or "backing_off" (engines blocked us).
OnWait = Callable[[str, float, Optional[str]], None]


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


class SearchThrottle:
    """Paces searches against an upstream that rate-limits by IP (SearXNG's engines).

    - One search at a time, with a random `min_delay`-`max_delay` second gap between them.
    - When a search comes back blocked, every search pauses for `backoff_base` seconds,
      doubling on each consecutive block up to `backoff_max`.

    State lives in Redis when a client is given, so every job and worker shares the same
    pacing and cooldown - a new job can't start hammering engines that just blocked the
    last one. Without Redis it falls back to in-process state (fine for scripts/tests).

    Defaults come from SEARCH_MIN_DELAY, SEARCH_MAX_DELAY, SEARCH_BACKOFF_BASE,
    SEARCH_BACKOFF_MAX and SEARCH_MAX_CONSECUTIVE_BLOCKS.
    """

    def __init__(
        self,
        redis: Optional[Any] = None,
        name: str = "searxng",
        min_delay: Optional[float] = None,
        max_delay: Optional[float] = None,
        backoff_base: Optional[float] = None,
        backoff_max: Optional[float] = None,
        max_consecutive_blocks: Optional[int] = None,
    ):
        self.redis = redis
        self.key = f"throttle:{name}"
        self.min_delay = min_delay if min_delay is not None else _env_float("SEARCH_MIN_DELAY", 2.0)
        self.max_delay = max_delay if max_delay is not None else _env_float("SEARCH_MAX_DELAY", 5.0)
        self.backoff_base = backoff_base if backoff_base is not None else _env_float("SEARCH_BACKOFF_BASE", 60.0)
        self.backoff_max = backoff_max if backoff_max is not None else _env_float("SEARCH_BACKOFF_MAX", 900.0)
        self.max_consecutive_blocks = (
            max_consecutive_blocks
            if max_consecutive_blocks is not None
            else int(_env_float("SEARCH_MAX_CONSECUTIVE_BLOCKS", 3))
        )
        self._memory: dict[str, str] = {}

    # -- storage ---------------------------------------------------------------

    def _read(self) -> dict[str, str]:
        if self.redis is None:
            return dict(self._memory)
        raw = self.redis.hgetall(self.key)
        return {
            (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
            for k, v in raw.items()
        }

    def _write(self, **fields: Any) -> None:
        values = {k: "" if v is None else str(v) for k, v in fields.items()}
        if self.redis is None:
            self._memory.update(values)
        else:
            self.redis.hset(self.key, mapping=values)

    def _lock(self):
        # Check-and-reserve must be atomic across workers; in-process it already is
        # (no await between the read and the write).
        if self.redis is None:
            return contextlib.nullcontext()
        return self.redis.lock(f"{self.key}:lock", timeout=10, blocking_timeout=10)

    @staticmethod
    def _float(state: dict[str, str], field: str) -> float:
        try:
            return float(state.get(field) or 0)
        except ValueError:
            return 0.0

    # -- public API --------------------------------------------------------------

    def _try_reserve(self) -> Optional[tuple[str, float, Optional[str]]]:
        """Claim the next search slot if it's free. Returns None if claimed, otherwise
        (state, wait_until, reason) describing what we're waiting on."""
        with self._lock():
            state = self._read()
            now = time.time()
            blocked_until = self._float(state, "blocked_until")
            next_allowed_at = self._float(state, "next_allowed_at")

            if blocked_until > now:
                return "backing_off", blocked_until, state.get("reason") or None
            if next_allowed_at > now:
                return "waiting", next_allowed_at, None

            self._write(next_allowed_at=now + random.uniform(self.min_delay, self.max_delay))
            return None

    async def wait(self, on_wait: Optional[OnWait] = None) -> None:
        """Wait until this caller may run a search, then claim the slot."""
        while True:
            waiting = self._try_reserve()
            if waiting is None:
                return
            state, until, reason = waiting
            if on_wait:
                on_wait(state, until, reason)
            # Re-check at least every 5s in case another worker extends/clears the cooldown
            await asyncio.sleep(min(max(until - time.time(), 0.05), 5.0))

    def record_success(self) -> None:
        """A search got through - the engines aren't blocking us, so reset the backoff streak."""
        self._write(streak=0)

    def record_block(self, reason: Optional[str]) -> dict:
        """A search was blocked: pause all searches, doubling the pause per consecutive block."""
        with self._lock():
            state = self._read()
            now = time.time()
            streak = int(self._float(state, "streak"))
            # A block long after the last one starts a fresh streak instead of compounding it
            if self._float(state, "blocked_until") < now - self.backoff_max:
                streak = 0
            streak += 1

            backoff = min(self.backoff_base * 2 ** (streak - 1), self.backoff_max)
            blocked_until = now + backoff
            self._write(streak=streak, blocked_until=blocked_until, reason=reason or "blocked")
        return {"streak": streak, "backoff": backoff, "blocked_until": blocked_until, "reason": reason}

    def status(self) -> dict:
        state = self._read()
        now = time.time()
        blocked_until = self._float(state, "blocked_until")
        blocked = blocked_until > now
        return {
            "blocked": blocked,
            "blocked_until": blocked_until if blocked else None,
            "reason": (state.get("reason") or None) if blocked else None,
            "streak": int(self._float(state, "streak")),
            "next_allowed_at": self._float(state, "next_allowed_at") or None,
            "min_delay": self.min_delay,
            "max_delay": self.max_delay,
            "max_consecutive_blocks": self.max_consecutive_blocks,
        }
