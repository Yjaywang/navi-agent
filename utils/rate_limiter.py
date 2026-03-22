"""Token-bucket rate limiter keyed by (guild_id, user_id)."""

import time
from dataclasses import dataclass


@dataclass
class _Bucket:
    tokens: float
    last_refill: float


class RateLimiter:
    """In-memory token bucket.  Tokens refill continuously over *refill_period*."""

    def __init__(self) -> None:
        self._buckets: dict[tuple[str, str], _Bucket] = {}

    def check(
        self,
        guild_id: str,
        user_id: str,
        max_tokens: int,
        refill_period: float = 3600.0,
    ) -> bool:
        """Return *True* (and consume 1 token) if allowed, *False* if rate-limited."""
        key = (guild_id, user_id)
        now = time.monotonic()
        bucket = self._buckets.get(key)

        if bucket is None:
            bucket = _Bucket(tokens=float(max_tokens), last_refill=now)
            self._buckets[key] = bucket

        # Continuous refill based on elapsed time
        elapsed = now - bucket.last_refill
        refill = elapsed * (max_tokens / refill_period)
        bucket.tokens = min(max_tokens, bucket.tokens + refill)
        bucket.last_refill = now

        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            return True
        return False

    def cleanup(self, max_age: float = 7200.0) -> None:
        """Remove buckets that haven't been touched for *max_age* seconds."""
        now = time.monotonic()
        stale = [k for k, b in self._buckets.items() if (now - b.last_refill) > max_age]
        for k in stale:
            del self._buckets[k]
