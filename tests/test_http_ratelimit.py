"""Tests for rate-limit-aware backoff in the http layer (no real network)."""

from __future__ import annotations

import httpx
import pytest

import skills_index.http as http_mod
from skills_index.http import _rate_limit_sleep, get_json


class _FakeResponse:
    def __init__(self, status_code=200, headers=None, json_data=None, request=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._json = json_data
        self.request = request or httpx.Request("GET", "https://api.github.com/x")

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"{self.status_code}", request=self.request, response=self
            )


def test_non_ratelimit_status_returns_none():
    assert _rate_limit_sleep(_FakeResponse(200)) is None
    assert _rate_limit_sleep(_FakeResponse(404)) is None
    # 403 without rate-limit headers is treated as a generic error (None).
    assert _rate_limit_sleep(_FakeResponse(403)) is None


def test_retry_after_header_is_honoured():
    resp = _FakeResponse(429, headers={"Retry-After": "12"})
    assert _rate_limit_sleep(resp) == 12.0


def test_x_ratelimit_reset_is_honoured(monkeypatch):
    import time

    now = 1_000_000.0
    monkeypatch.setattr(time, "time", lambda: now)
    # reset is 30s in the future -> expect 30.0 (capped well under MAX_BACKOFF).
    resp = _FakeResponse(403, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(now + 30)})
    assert _rate_limit_sleep(resp) == 30.0


def test_reset_in_past_clamped_to_zero(monkeypatch):
    import time

    now = 1_000_000.0
    monkeypatch.setattr(time, "time", lambda: now)
    resp = _FakeResponse(403, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(now - 100)})
    assert _rate_limit_sleep(resp) == 0.0


def test_get_json_retries_on_ratelimit_without_raising(monkeypatch):
    sleeps = []
    monkeypatch.setattr(http_mod.time, "sleep", lambda s: sleeps.append(s))

    calls = {"n": 0}

    def fake_get(url):
        calls["n"] += 1
        if calls["n"] < 3:
            return _FakeResponse(429, headers={"Retry-After": "1"})
        return _FakeResponse(200, json_data={"ok": True})

    class _FakeClient:
        def get(self, url):
            return fake_get(url)

    result = get_json(_FakeClient(), "x")  # type: ignore[arg-type]
    assert result == {"ok": True}
    assert calls["n"] == 3  # 2 rate-limited + 1 success
    # Slept on the two 429s (Retry-After=1 each); no hard failure.
    assert sleeps == [1.0, 1.0]


def test_get_json_gives_up_after_retries(monkeypatch):
    monkeypatch.setattr(http_mod.time, "sleep", lambda s: None)

    def fake_get(url):
        return _FakeResponse(403, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "9999999999"})

    class _FakeClient:
        def get(self, url):
            return fake_get(url)

    from skills_index.http import HttpError

    with pytest.raises(HttpError):
        get_json(_FakeClient(), "x")  # type: ignore[arg-type]
