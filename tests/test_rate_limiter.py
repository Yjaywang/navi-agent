"""Tests for utils/rate_limiter.py."""

import time
from unittest.mock import patch

from utils.rate_limiter import RateLimiter


class TestRateLimiter:
    def test_initial_check_succeeds(self):
        rl = RateLimiter()
        assert rl.check("g", "u", max_tokens=5) is True

    def test_exhausts_tokens(self):
        rl = RateLimiter()
        for _ in range(5):
            assert rl.check("g", "u", max_tokens=5) is True
        assert rl.check("g", "u", max_tokens=5) is False

    def test_independent_keys(self):
        rl = RateLimiter()
        for _ in range(3):
            rl.check("g", "u1", max_tokens=3)
        assert rl.check("g", "u1", max_tokens=3) is False
        assert rl.check("g", "u2", max_tokens=3) is True

    def test_token_refill(self):
        rl = RateLimiter()
        # Exhaust tokens
        for _ in range(5):
            rl.check("g", "u", max_tokens=5, refill_period=100.0)

        # Simulate time passing (enough to refill 1 token)
        bucket = rl._buckets[("g", "u")]
        bucket.last_refill -= 25.0  # 25s elapsed, refill_rate = 5/100 = 0.05/s → 1.25 tokens

        assert rl.check("g", "u", max_tokens=5, refill_period=100.0) is True

    def test_cleanup(self):
        rl = RateLimiter()
        rl.check("g", "u", max_tokens=5)
        bucket = rl._buckets[("g", "u")]
        bucket.last_refill = time.monotonic() - 8000
        rl.cleanup(max_age=7200.0)
        assert ("g", "u") not in rl._buckets
