"""Tests for fetch pipeline behavior (mocked HTTP layer, no real network)."""

from __future__ import annotations

import pytest

from skills_index import fetch
from skills_index.http import HttpError

# Fetch tests never need to sleep between pages.
TEST_PAUSE = 0.0


def _page(*, skill_ids: list[str], has_more: bool = False) -> dict:
    return {
        "skills": [{"skillId": sid, "name": sid} for sid in skill_ids],
        "hasMore": has_more,
        "total": len(skill_ids),
    }


def test_fetch_all_skips_failed_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """An isolated failing page is skipped; fetching continues and reports it."""
    requested: list[int] = []

    def fake_get_json(client: object, url: str) -> dict:
        page = int(url.rsplit("/", 1)[-1])
        requested.append(page)
        if page == 1:
            raise HttpError("boom")
        return _page(skill_ids=[f"s{page}"], has_more=(page < 2))

    monkeypatch.setattr(fetch, "get_json", fake_get_json)
    monkeypatch.setattr(fetch, "POLITE_PAUSE", TEST_PAUSE)

    skills, failed = fetch.fetch_all(0)

    assert [s["skillId"] for s in skills] == ["s0", "s2"]
    assert failed == [1]
    assert requested == [0, 1, 2]


def test_fetch_all_aborts_after_consecutive_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fetching stops after MAX_CONSECUTIVE_PAGE_FAILURES in a row."""
    requested: list[int] = []

    def fake_get_json(client: object, url: str) -> dict:
        page = int(url.rsplit("/", 1)[-1])
        requested.append(page)
        if page < 3:
            raise HttpError("boom")
        return _page(skill_ids=[f"s{page}"], has_more=False)

    monkeypatch.setattr(fetch, "get_json", fake_get_json)
    monkeypatch.setattr(fetch, "POLITE_PAUSE", TEST_PAUSE)

    skills, failed = fetch.fetch_all(0)

    assert skills == []
    assert failed == [0, 1, 2]
    # Stopped before ever reaching page 3.
    assert requested == [0, 1, 2]
    assert fetch.MAX_CONSECUTIVE_PAGE_FAILURES == 3


def test_fetch_all_respects_max_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """max_pages caps the number of fetched pages."""
    requested: list[int] = []

    def fake_get_json(client: object, url: str) -> dict:
        page = int(url.rsplit("/", 1)[-1])
        requested.append(page)
        return _page(skill_ids=[f"s{page}"], has_more=True)

    monkeypatch.setattr(fetch, "get_json", fake_get_json)
    monkeypatch.setattr(fetch, "POLITE_PAUSE", TEST_PAUSE)

    skills, failed = fetch.fetch_all(2)

    assert [s["skillId"] for s in skills] == ["s0", "s1"]
    assert failed == []
    assert requested == [0, 1]
