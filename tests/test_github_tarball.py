"""End-to-end tests for the codeload tarball scan path (no real network).

Verifies that `get_skill_blobs` downloads a repo tarball exactly once and that
`get_skill_descriptions` reuses the cached contents without further requests.
"""

from __future__ import annotations

import io
import tarfile

from skills_index import github
from skills_index.github import get_skill_blobs, get_skill_descriptions


def _make_tarball(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(f"repo-sha/{name}")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class _FakeResponse:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        pass


class _FakeClient:
    """Records requested URLs and serves a canned tarball."""

    def __init__(self, content: bytes) -> None:
        self._content = content
        self.requests: list[str] = []

    def get(self, url: str) -> _FakeResponse:
        self.requests.append(url)
        return _FakeResponse(self._content)


def _reset_cache(monkeypatch) -> None:
    monkeypatch.setattr(github, "_tarball_scan", {})


def test_get_skill_blobs_downloads_tarball_once(monkeypatch) -> None:
    _reset_cache(monkeypatch)
    raw = _make_tarball(
        {
            "skills/foo/SKILL.md": b"---\ndescription: Foo\n---\n",
            "skills/bar/SKILL.md": b"---\ndescription: Bar\n---\n",
        }
    )
    client = _FakeClient(raw)
    blobs = get_skill_blobs("owner/repo", "main", client=client)  # type: ignore[arg-type]

    assert client.requests == ["https://codeload.github.com/owner/repo/tar.gz/main"]
    assert set(blobs) == {"foo", "bar"}
    assert blobs["foo"][0] == "skills/foo"


def test_get_skill_descriptions_reuses_cached_tarball(monkeypatch) -> None:
    _reset_cache(monkeypatch)
    raw = _make_tarball(
        {
            "skills/foo/SKILL.md": b"---\ndescription: Foo\n---\n",
            "skills/bar/SKILL.md": b"---\ndescription: Bar\n---\n",
        }
    )
    client = _FakeClient(raw)

    blobs = get_skill_blobs("owner/repo", "main", client=client)  # type: ignore[arg-type]
    # Fetching descriptions must not issue a second tarball request.
    descs = get_skill_descriptions("owner/repo", blobs, client=client)  # type: ignore[arg-type]

    assert descs == {"foo": "Foo", "bar": "Bar"}
    assert client.requests == ["https://codeload.github.com/owner/repo/tar.gz/main"]
