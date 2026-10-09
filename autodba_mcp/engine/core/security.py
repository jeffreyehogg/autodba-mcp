"""
AutoDBA security and rate-limiting utilities.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Mapping


class RateLimiter:
    """
    Thread-safe, fixed-window in-memory rate limiter.
    Provides best-effort enforcement per serverless execution context.
    """

    def __init__(self, limit: int, window_s: int):
        self.limit = limit
        self.window_s = window_s
        self._lock = threading.Lock()
        # key -> (window_start_epoch, count)
        self._buckets: dict[str, tuple[float, int]] = {}

    def _cleanup_unlocked(self, now: float) -> None:
        """Prune expired buckets periodically to bound memory."""
        expired = [
            k
            for k, (win_start, _) in self._buckets.items()
            if now - win_start > self.window_s * 2
        ]
        for k in expired:
            del self._buckets[k]

    def hit(self, key: str) -> tuple[bool, int, int]:
        """
        Record an access attempt for key.
        Returns: (allowed, remaining, reset_seconds)
        """
        now = time.time()
        with self._lock:
            self._cleanup_unlocked(now)
            entry = self._buckets.get(key)
            if entry is None or (now - entry[0] >= self.window_s):
                # New window
                self._buckets[key] = (now, 1)
                remaining = max(0, self.limit - 1)
                reset_seconds = self.window_s
                return (True, remaining, reset_seconds)

            win_start, count = entry
            elapsed = now - win_start
            reset_seconds = max(1, int(self.window_s - elapsed))

            if count < self.limit:
                self._buckets[key] = (win_start, count + 1)
                remaining = self.limit - (count + 1)
                return (True, remaining, reset_seconds)
            else:
                return (False, 0, reset_seconds)

    def peek(self, key: str) -> tuple[int, int]:
        """
        Inspect quota without incrementing count.
        Returns: (remaining, reset_seconds)
        """
        now = time.time()
        with self._lock:
            entry = self._buckets.get(key)
            if entry is None or (now - entry[0] >= self.window_s):
                return (self.limit, self.window_s)
            win_start, count = entry
            elapsed = now - win_start
            reset_seconds = max(1, int(self.window_s - elapsed))
            remaining = max(0, self.limit - count)
            return (remaining, reset_seconds)


def client_ip(headers: Mapping[str, str], fallback: str | None = "127.0.0.1") -> str:
    """
    Extract client IP from standard proxy headers (X-Forwarded-For, X-Real-IP).
    """
    # Look for x-forwarded-for case-insensitively
    xff = None
    x_real = None
    for k, v in headers.items():
        lk = k.lower()
        if lk == "x-forwarded-for":
            xff = v
        elif lk == "x-real-ip":
            x_real = v

    if xff:
        # First IP in comma-separated list
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            return parts[0]

    if x_real and x_real.strip():
        return x_real.strip()

    return fallback or "127.0.0.1"


_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")


def sanitize_sql(sql: str) -> str:
    """
    Normalize CRLF, strip non-printable control characters (preserving tab and newline),
    and remove leading/trailing whitespace.
    Raises ValueError if input is empty or whitespace only.
    """
    if not sql or not sql.strip():
        raise ValueError("SQL statement cannot be empty.")

    # Normalize CRLF -> LF
    normalized = sql.replace("\r\n", "\n").replace("\r", "\n")
    # Strip bad control characters
    cleaned = _CONTROL_CHARS_RE.sub("", normalized).strip()

    if not cleaned:
        raise ValueError("SQL statement cannot be empty after sanitization.")

    return cleaned


_API_KEY_RE = re.compile(r"^[A-Za-z0-9_\-]{20,200}$")


def is_plausible_api_key(key: str | None) -> bool:
    """
    Basic sanity check for Google Gemini / OpenAI style API key strings.
    Never logs or echoes the actual key value.
    """
    if not key or not isinstance(key, str):
        return False
    return bool(_API_KEY_RE.match(key.strip()))
