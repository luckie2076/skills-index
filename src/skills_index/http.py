"""Thin httpx wrapper: retries, GitHub auth, and friendly rate-limit hints."""

from __future__ import annotations

import time
from typing import Any

import httpx

from .config import GITHUB_API, load_github_token

RETRIES = 3
TIMEOUT = 30.0
USER_AGENT = "skills-index"
POLITE_PAUSE = 0.3  # seconds between paginated requests


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


def get_json(client: httpx.Client, url: str) -> Any:
    """GET `url` and parse JSON, retrying on transient errors."""
    last_err: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            last_err = exc
            print(f"  [retry {attempt}/{RETRIES}] {url}: {exc}")
            time.sleep(2 * attempt)
    raise HttpError(f"request failed after {RETRIES} retries: {url}") from last_err


def new_github_client(token: str | None = None) -> httpx.Client:
    """Convenience: a GitHub-authenticated client."""
    return build_client(token or load_github_token(), base_url=GITHUB_API)
