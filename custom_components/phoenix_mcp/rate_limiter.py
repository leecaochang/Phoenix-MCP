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

    When allowed with limiting enabled, send the X-RateLimit-* headers. When refused, send
    HTTP 429 with Retry-After set to ``retry_after``, without the X-RateLimit-* headers.
    """

    allowed: bool
    rate_limiting_enabled: bool
    limit: int = 0
    remaining: int = 0
    reset: int = 0
    retry_after: int = 0
    # When an allowed request was recorded, so charge_burst can find it.
    stamp: float | None = None


class RateLimiter:
    """Sliding window rate limiter keyed by access key ID.

    In-memory only; call destroy() on revocation or archival. Per request, it evicts old
    timestamps, denies at rate_limit_requests in the window or rate_limit_burst in the burst
    window without recording, else records and allows.

    Session handshake messages (a client's ``notifications/initialized`` and its session
    close) do no work, yet a client that opens several sessions at once sends a dozen of
    them within a second. They are kept in a second, quiet record per key: it counts toward
    the per-minute ceiling like any request, and never toward the burst.
    """

    def __init__(self) -> None:
        self._windows: dict[str, deque[float]] = {}
        self._quiet: dict[str, deque[float]] = {}

    def _evict(self, access_key_id: str, now: float) -> tuple[deque[float], deque[float]]:
        """The key's two records, with entries older than the minute window dropped."""
        window = self._windows.setdefault(access_key_id, deque())
        quiet = self._quiet.setdefault(access_key_id, deque())
        cutoff = now - WINDOW_SECONDS
        for record in (window, quiet):
            while record and record[0] <= cutoff:
                record.popleft()
        return window, quiet

    def check(
        self,
        access_key_id: str,
        rate_limit_requests: int,
        rate_limit_burst: int,
        *,
        burst: bool = True,
    ) -> RateLimitResult:
        """Check and record a request against the rate limit for access_key_id.

        Returns a RateLimitResult with allowed=True if the request may proceed.
        A denied result does NOT record the request in the window. With ``burst`` False
        (a handshake message) only the per-minute ceiling applies, and the request is
        recorded where the burst does not count it.
        """
        if rate_limit_requests == 0:
            return RateLimitResult(allowed=True, rate_limiting_enabled=False)

        now = time.monotonic()
        wall_now = time.time()
        burst_cutoff = now - BURST_WINDOW_SECONDS

        # Step 1: evict stale entries
        window, quiet = self._evict(access_key_id, now)
        used = len(window) + len(quiet)
        first = min(window[0] if window else now, quiet[0] if quiet else now)

        # Step 2: sliding window check
        if used >= rate_limit_requests:
            oldest = first
            retry_after = math.ceil(oldest + WINDOW_SECONDS - now)
            return RateLimitResult(
                allowed=False,
                rate_limiting_enabled=True,
                limit=rate_limit_requests,
                remaining=0,
                reset=int(wall_now + (oldest + WINDOW_SECONDS - now)),
                retry_after=max(1, retry_after),
            )

        if not burst:
            quiet.append(now)
            return RateLimitResult(
                allowed=True,
                rate_limiting_enabled=True,
                limit=rate_limit_requests,
                remaining=rate_limit_requests - used - 1,
                reset=int(wall_now + (first + WINDOW_SECONDS - now)),
                retry_after=0,
                stamp=now,
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
                reset = int(wall_now + (first + WINDOW_SECONDS - now))
                return RateLimitResult(
                    allowed=False,
                    rate_limiting_enabled=True,
                    limit=rate_limit_requests,
                    remaining=max(0, rate_limit_requests - used),
                    reset=reset,
                    retry_after=max(1, retry_after),
                )

        # Step 4: record and return
        window.append(now)
        reset = int(wall_now + (first + WINDOW_SECONDS - now))
        return RateLimitResult(
            allowed=True,
            rate_limiting_enabled=True,
            limit=rate_limit_requests,
            remaining=rate_limit_requests - used - 1,
            reset=reset,
            retry_after=0,
            stamp=now,
        )

    def charge_burst(
        self,
        access_key_id: str,
        rate_limit_requests: int,
        rate_limit_burst: int,
        stamp: float | None,
    ) -> RateLimitResult:
        """Apply the burst to a request ``check(burst=False)`` already recorded for the minute.

        For a request whose kind is known only once its body is parsed: a handshake message
        never calls this, any other request does. Refused, the request is taken off the minute
        too, as ``check`` records nothing it refuses.
        """
        if rate_limit_requests == 0:
            return RateLimitResult(allowed=True, rate_limiting_enabled=False)
        now = time.monotonic()
        wall_now = time.time()
        window, quiet = self._evict(access_key_id, now)
        if stamp is not None and stamp in quiet:
            quiet.remove(stamp)
        else:
            stamp = now  # Expired or destroyed in between: charge it afresh.
        first = min(window[0] if window else now, quiet[0] if quiet else now, stamp)
        if rate_limit_burst > 0:
            in_burst = [t for t in window if t > now - BURST_WINDOW_SECONDS]
            if len(in_burst) >= rate_limit_burst:
                return RateLimitResult(
                    allowed=False,
                    rate_limiting_enabled=True,
                    limit=rate_limit_requests,
                    remaining=max(0, rate_limit_requests - len(window) - len(quiet)),
                    reset=int(wall_now + (first + WINDOW_SECONDS - now)),
                    retry_after=max(1, math.ceil(in_burst[0] + BURST_WINDOW_SECONDS - now)),
                )
        # Keep the window in time order: a later request may have been recorded meanwhile.
        at = len(window)
        while at and window[at - 1] > stamp:
            at -= 1
        window.insert(at, stamp)
        return RateLimitResult(
            allowed=True,
            rate_limiting_enabled=True,
            limit=rate_limit_requests,
            remaining=max(0, rate_limit_requests - len(window) - len(quiet)),
            reset=int(wall_now + (first + WINDOW_SECONDS - now)),
            retry_after=0,
            stamp=stamp,
        )

    def charge_batch(
        self,
        access_key_id: str,
        rate_limit_requests: int,
        rate_limit_burst: int,
        extra: int,
    ) -> RateLimitResult:
        """Charge ``extra`` further units, or refuse the whole batch.
        A preflight before any item runs, checking both ceilings; ``extra`` is items minus
        one, as ``check`` charged the request. Returns an allowed or denied result whose
        headers the caller must send.
        """
        if rate_limit_requests == 0:
            return RateLimitResult(allowed=True, rate_limiting_enabled=False)

        now = time.monotonic()
        wall_now = time.time()
        window, quiet = self._evict(access_key_id, now)
        first = min(window[0] if window else now, quiet[0] if quiet else now)

        def _denied(oldest: float, wait_until: float) -> RateLimitResult:
            return RateLimitResult(
                allowed=False,
                rate_limiting_enabled=True,
                limit=rate_limit_requests,
                remaining=max(0, rate_limit_requests - len(window) - len(quiet)),
                reset=int(wall_now + (oldest + WINDOW_SECONDS - now)),
                retry_after=max(1, math.ceil(wait_until - now)),
            )

        if extra > 0 and len(window) + len(quiet) + extra > rate_limit_requests:
            oldest = first
            return _denied(oldest, oldest + WINDOW_SECONDS)

        if extra > 0 and rate_limit_burst > 0:
            burst_cutoff = now - BURST_WINDOW_SECONDS
            in_burst = sum(1 for t in window if t > burst_cutoff)
            if in_burst + extra > rate_limit_burst:
                oldest = first
                # The whole batch is charged at `now`, so waiting for the current
                # burst window to clear is the soonest it could be accepted.
                return _denied(oldest, now + BURST_WINDOW_SECONDS)

        if extra > 0:
            window.extend([now] * extra)
        return RateLimitResult(
            allowed=True,
            rate_limiting_enabled=True,
            limit=rate_limit_requests,
            remaining=max(0, rate_limit_requests - len(window) - len(quiet)),
            reset=int(wall_now + (first + WINDOW_SECONDS - now)),
            retry_after=0,
        )

    def destroy(self, access_key_id: str) -> None:
        """Destroy rate limit state for a single access key (call on revocation/archival)."""
        self._windows.pop(access_key_id, None)
        self._quiet.pop(access_key_id, None)

    def destroy_all(self) -> None:
        """Destroy all rate limit state (call on wipe action)."""
        self._windows.clear()
        self._quiet.clear()

    def active_access_key_count(self) -> int:
        """Return the number of access keys with active rate limit windows."""
        return len(self._windows)
