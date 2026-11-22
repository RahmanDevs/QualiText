"""In-memory + optional Redis sliding-window rate limiter for authentication endpoints."""

import threading
import time
from collections import deque


class LoginRateLimiter:
    """Thread-safe sliding-window tracker for failed authentication attempts."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failures: dict[tuple[str, str], deque[float]] = {}

    def _prune(self, dq: deque[float], cutoff: float) -> None:
        while dq and dq[0] <= cutoff:
            dq.popleft()

    def is_blocked(
        self,
        ip: str,
        email: str,
        max_attempts: int,
        window_seconds: int,
    ) -> tuple[bool, int]:
        """Return `(blocked, retry_after_seconds)` for the given IP and email."""
        if max_attempts <= 0 or window_seconds <= 0:
            return False, 0

        now = time.monotonic()
        cutoff = now - window_seconds
        norm_ip = ip or "unknown"
        norm_email = email.strip().lower()

        with self._lock:
            checks = [
                ((norm_ip, norm_email), max_attempts),
                ((norm_ip, "*"), max(max_attempts * 3, max_attempts)),
            ]
            for key, limit in checks:
                dq = self._failures.get(key)
                if not dq:
                    continue
                self._prune(dq, cutoff)
                if not dq:
                    del self._failures[key]
                    continue
                if len(dq) >= limit:
                    retry_after = max(1, int((dq[0] + window_seconds) - now) + 1)
                    return True, retry_after
        return False, 0

    def record_failure(self, ip: str, email: str, window_seconds: int) -> None:
        """Record a failed login timestamp under both `(ip, email)` and `(ip, '*')`."""
        now = time.monotonic()
        cutoff = now - max(1, window_seconds)
        norm_ip = ip or "unknown"
        norm_email = email.strip().lower()

        with self._lock:
            for key in ((norm_ip, norm_email), (norm_ip, "*")):
                dq = self._failures.setdefault(key, deque())
                self._prune(dq, cutoff)
                dq.append(now)

    def clear_failures(self, ip: str, email: str) -> None:
        """Clear failed login counters for `(ip, email)` after a successful login."""
        norm_ip = ip or "unknown"
        norm_email = email.strip().lower()
        with self._lock:
            self._failures.pop((norm_ip, norm_email), None)

    def reset(self) -> None:
        """Clear all tracked failure state (used in automated tests)."""
        with self._lock:
            self._failures.clear()


login_rate_limiter = LoginRateLimiter()
