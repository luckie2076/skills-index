"""Thin httpx wrapper: retries, GitHub auth, rate-limit-aware backoff."""

from __future__ import annotations

import time
from typing import Any

import httpx

from .config import GITHUB_API, load_github_token

RETRIES = 5
TIMEOUT = 30.0
USER_AGENT = "skills-index"
POLITE_PAUSE = 0.3  # seconds between paginated requests
MAX_BACKOFF = 60.0  # cap a single backoff wait


class HttpError(RuntimeError):
    """Raised when a request fails after all retries."""


def build_client(token: str = "", *, base_url: str = "") -> httpx.Client:
    """Create a configured httpx client.

    `token` authenticates GitHub requests (raises the 60/h limit to 5000/h).
    `base_url` scopes relative paths (e.g. ``GITHUB_API``).
    """
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.Client(
        base_url=base_url,
        headers=headers,
        timeout=TIMEOUT,
        follow_redirects=True,
    )


def _rate_limit_sleep(resp: httpx.Response) -> float | None:
    """Return how long to sleep for a rate-limited response, or None.

    Honours GitHub's ``Retry-After`` header first, then falls back to the
    ``X-RateLimit-Reset`` timestamp. Returns ``None`` when the response is not
    a rate-limit response (so other errors are not misclassified).
    """
    status = resp.status_code
    if status not in (403, 429):
        return None
    # A 403 can mean rate limit or other forbidden; only treat it as a rate
    # limit when GitHub says so (body message or explicit reset header).
    remaining = resp.headers.get("X-RateLimit-Remaining")
    reset = resp.headers.get("X-RateLimit-Reset")
    retry_after = resp.headers.get("Retry-After")
    if retry_after is not None:
        try:
            return min(float(retry_after), MAX_BACKOFF)
        except ValueError:
            pass
    if remaining == "0" and reset is not None:
        try:
            delay = float(reset) - time.time()
            return min(max(delay, 0.0), MAX_BACKOFF)
        except ValueError:
            pass
    # 429 without Retry-After, or a 403 rate-limit with no headers: let the
    # caller's exponential backoff handle it (caller checks status).
    if status == 429:
        return 0.0
    return None


def get_json(client: httpx.Client, url: str) -> Any:
    """GET `url` and parse JSON, retrying with rate-limit-aware backoff.

    Transient/5xx errors use exponential backoff. Rate-limit responses (403
    with exhausted quota, 429) honour GitHub's ``Retry-After`` /
    ``X-RateLimit-Reset`` so we wait exactly as long as GitHub asks.
    """
    last_err: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            resp = client.get(url)
            if resp.status_code < 400:
                return resp.json()
            rl_sleep = _rate_limit_sleep(resp)
            if rl_sleep is not None:
                wait = rl_sleep or (2.0 * attempt)
                print(
                    f"  [rate-limit {resp.status_code}] {url}: "
                    f"sleeping {wait:.1f}s (attempt {attempt}/{RETRIES})"
                )
                time.sleep(wait)
                last_err = httpx.HTTPStatusError(
                    f"{resp.status_code} on {url}",
                    request=resp.request,
                    response=resp,
                )
                continue
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            last_err = exc
            wait = 2.0 * attempt
            print(f"  [retry {attempt}/{RETRIES}] {url}: {exc}; sleeping {wait:.1f}s")
            time.sleep(wait)
    raise HttpError(f"request failed after {RETRIES} retries: {url}") from last_err


def new_github_client(token: str | None = None) -> httpx.Client:
    """Convenience: a GitHub-authenticated client."""
    return build_client(token or load_github_token(), base_url=GITHUB_API)
