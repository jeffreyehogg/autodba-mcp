"""
Tests for security utilities, rate limiting, and SQL sanitization.
"""

import pytest

from autodba_mcp.engine.core.security import (
    RateLimiter,
    client_ip,
    is_plausible_api_key,
    sanitize_sql,
)


def test_rate_limiter_allows_and_blocks():
    limiter = RateLimiter(limit=3, window_s=60)
    allowed, rem, _ = limiter.hit("ip1")
    assert allowed and rem == 2
    allowed, rem, _ = limiter.hit("ip1")
    assert allowed and rem == 1
    allowed, rem, _ = limiter.hit("ip1")
    assert allowed and rem == 0
    # 4th should block
    allowed, rem, _ = limiter.hit("ip1")
    assert not allowed and rem == 0


def test_sanitize_sql_normalizes():
    raw = "SELECT * FROM Leads\r\nWHERE Id = 1\x00;"
    cleaned = sanitize_sql(raw)
    assert "\r\n" not in cleaned
    assert "\x00" not in cleaned
    assert cleaned.startswith("SELECT")


def test_sanitize_sql_empty_raises():
    with pytest.raises(ValueError):
        sanitize_sql("   ")


def test_is_plausible_api_key():
    assert is_plausible_api_key("AIzaSyD-1234567890abcdefghijklmnop")
    assert not is_plausible_api_key("short")
    assert not is_plausible_api_key(None)
    assert not is_plausible_api_key("bad key with spaces!@#$%^")


def test_client_ip_extraction():
    headers = {"X-Forwarded-For": "203.0.113.195, 70.41.3.18"}
    assert client_ip(headers) == "203.0.113.195"
    headers_real = {"X-Real-IP": "198.51.100.1"}
    assert client_ip(headers_real) == "198.51.100.1"
