"""Per-access-key sliding window rate limiter for Phoenix MCP. In-memory only, no I/O."""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass

WINDOW_SECONDS = 60.0
BURST_WINDOW_SECONDS = 1.0


@dataclass
class RateLimitResult:
    """Result of a rate limit check.

    When allowed is True and rate_limiting_enabled is True, the view should
    include X-RateLimit-Limit, X-RateLimit-Remaining, and X-RateLimit-Reset
    response headers.

    When allowed is False, the view should return HTTP 429 with a Retry-After
    header set to retry_after. The X-RateLimit-* headers are omitted on 429.
    """

    allowed: bool
    rate_limiting_enabled: bool
    limit: int = 0
    remaining: int = 0
    reset: int = 0
    retry_after: int = 0


class RateLimiter:
    """Sliding window rate limiter keyed by access key ID.

    State is in-memory only and is lost on HA restart. Destroy an access key's state
    immediately on revocation or archival by calling destroy(access_key_id).

    Algorithm per request (when rate limiting is enabled):
      1. Evict timestamps older than WINDOW_SECONDS from the per-access-key deque.
      2. If len(window) >= rate_limit_requests, return denied. Do not record.
      3. Count timestamps within the last BURST_WINDOW_SECONDS. If that count
         is >= rate_limit_burst (and burst > 0), return denied. Do not record.
      4. Record the current timestamp and return allowed.
    """

    def __init__(self) -> None:
        self._windows: dict[str, deque[float]] = {}

    def check(
        self,
        access_key_id: str,
        rate_limit_requests: int,
        rate_limit_burst: int,
    ) -> RateLimitResult:
        """Check and record a request against the rate limit for access_key_id.

        Returns a RateLimitResult with allowed=True if the request may proceed.
        A denied result does NOT record the request in the window.
        """
        if rate_limit_requests == 0:
            return RateLimitResult(allowed=True, rate_limiting_enabled=False)

        now = time.monotonic()
        wall_now = time.time()
        window_cutoff = now - WINDOW_SECONDS
        burst_cutoff = now - BURST_WINDOW_SECONDS

        if access_key_id not in self._windows:
            self._windows[access_key_id] = deque()
        window = self._windows[access_key_id]

        # Step 1: evict stale entries
        while window and window[0] <= window_cutoff:
            window.popleft()

        # Step 2: sliding window check
        if len(window) >= rate_limit_requests:
            oldest = window[0]
            retry_after = math.ceil(oldest + WINDOW_SECONDS - now)
            return RateLimitResult(
                allowed=False,
                rate_limiting_enabled=True,
                limit=rate_limit_requests,
                remaining=0,
                reset=int(wall_now + (oldest + WINDOW_SECONDS - now)),
                retry_after=max(1, retry_after),
            )

        # Step 3: burst check. The deque is time-ordered, so scan from the right
        # and stop at the burst window boundary; the oldest in-burst entry is then
        # indexed directly.
        if rate_limit_burst > 0:
            last_second_count = 0
            for t in reversed(window):
                if t <= burst_cutoff:
                    break
                last_second_count += 1
            if last_second_count >= rate_limit_burst:
                oldest_in_burst = window[len(window) - last_second_count]
                retry_after = math.ceil(oldest_in_burst + BURST_WINDOW_SECONDS - now)
                reset = int(wall_now + (window[0] + WINDOW_SECONDS - now)) if window else int(wall_now + WINDOW_SECONDS)
                return RateLimitResult(
                    allowed=False,
                    rate_limiting_enabled=True,
                    limit=rate_limit_requests,
                    remaining=max(0, rate_limit_requests - len(window)),
                    reset=reset,
                    retry_after=max(1, retry_after),
                )

        # Step 4: record and return
        window.append(now)
        reset = int(wall_now + (window[0] + WINDOW_SECONDS - now))
        return RateLimitResult(
            allowed=True,
            rate_limiting_enabled=True,
            limit=rate_limit_requests,
            remaining=rate_limit_requests - len(window),
            reset=reset,
            retry_after=0,
        )

    def charge_batch(
        self,
        access_key_id: str,
        rate_limit_requests: int,
        rate_limit_burst: int,
        extra: int,
    ) -> RateLimitResult:
        """Charge `extra` further units, or refuse the whole batch.

        A JSON-RPC batch is ONE HTTP request carrying up to MAX_BATCH_ITEMS
        calls, so `check` alone would charge it once and batching would
        multiply the configured ceiling (60/min times 50 items is 3,000 calls a
        minute). `check` still charges the request itself, so `extra` is the
        remainder (items minus one).

        This is a PREFLIGHT, run before any item is dispatched, and the refusal
        is the point: charging without refusing lets every item execute and only
        then throttles, running a 50-item batch 40 operations past a 10/min
        ceiling. A refusal leaves no half-applied batch. BOTH ceilings are
        checked: a batch lands in one instant, so the minute limit alone would
        let it exceed the per-second burst setting many times over.

        Returns an allowed result (limiting disabled, or both budgets cover the
        batch) or a denied one carrying `retry_after`. The caller must send the
        returned result's headers: the one from `check` understates the spend.
        """
        if rate_limit_requests == 0:
            return RateLimitResult(allowed=True, rate_limiting_enabled=False)

        now = time.monotonic()
        wall_now = time.time()
        window = self._windows.setdefault(access_key_id, deque())
        while window and window[0] <= now - WINDOW_SECONDS:
            window.popleft()

        def _denied(oldest: float, wait_until: float) -> RateLimitResult:
            return RateLimitResult(
                allowed=False,
                rate_limiting_enabled=True,
                limit=rate_limit_requests,
                remaining=max(0, rate_limit_requests - len(window)),
                reset=int(wall_now + (oldest + WINDOW_SECONDS - now)),
                retry_after=max(1, math.ceil(wait_until - now)),
            )

        if extra > 0 and len(window) + extra > rate_limit_requests:
            oldest = window[0] if window else now
            return _denied(oldest, oldest + WINDOW_SECONDS)

        if extra > 0 and rate_limit_burst > 0:
            burst_cutoff = now - BURST_WINDOW_SECONDS
            in_burst = sum(1 for t in window if t > burst_cutoff)
            if in_burst + extra > rate_limit_burst:
                oldest = window[0] if window else now
                # The whole batch is charged at `now`, so waiting for the current
                # burst window to clear is the soonest it could be accepted.
                return _denied(oldest, now + BURST_WINDOW_SECONDS)

        if extra > 0:
            window.extend([now] * extra)
        return RateLimitResult(
            allowed=True,
            rate_limiting_enabled=True,
            limit=rate_limit_requests,
            remaining=max(0, rate_limit_requests - len(window)),
            reset=int(wall_now + (window[0] + WINDOW_SECONDS - now)) if window else int(wall_now + WINDOW_SECONDS),
            retry_after=0,
        )

    def destroy(self, access_key_id: str) -> None:
        """Destroy rate limit state for a single access key (call on revocation/archival)."""
        self._windows.pop(access_key_id, None)

    def destroy_all(self) -> None:
        """Destroy all rate limit state (call on wipe action)."""
        self._windows.clear()

    def active_access_key_count(self) -> int:
        """Return the number of access keys with active rate limit windows."""
        return len(self._windows)
