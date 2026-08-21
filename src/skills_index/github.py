"""GitHub API surface: repo metadata and SKILL.md content.

Only the REST metadata endpoint (per-repo pushed_at / default_branch) and the
codeload tarball endpoint are used. Repositories are never cloned; each repo's
SKILL.md files are read from a single tarball download, which is NOT billed
against the REST API rate limit -- keeping full scans well under the Actions
GITHUB_TOKEN quota (1000 req/h) and any personal PAT (5000 req/h).
"""

from __future__ import annotations

import hashlib
import io
import tarfile
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import httpx
import yaml

from .http import get_json, new_github_client

# codeload serves archive downloads and is not part of the REST API rate limit.
CODELOAD = "https://codeload.github.com"


# Process-wide cache (valid for a single run only), keyed by source only so the
# chosen httpx.Client is never pinned in the cache (avoids leaking connections
# and keeps the key stable regardless of which caller-supplied client is used).
_repo_info_cache: dict[str, tuple[str, str, int]] = {}


def _repo_info(source: str, *, client: httpx.Client | None = None) -> tuple[str, str, int]:
    """Return (pushed_at, default_branch, stars) for `source`, cached for the run."""
    cached = _repo_info_cache.get(source)
    if cached is not None:
        return cached
    owner, repo = _split(source)
    c = client or new_github_client()
    data = get_json(c, f"/repos/{owner}/{repo}")
    pushed = data.get("pushed_at") or data.get("updated_at") or ""
    branch = str(data.get("default_branch", "main"))
    stars = int(data.get("stargazers_count") or 0)
    _repo_info_cache[source] = (pushed, branch, stars)
    return _repo_info_cache[source]


def _split(source: str) -> tuple[str, str]:
    owner, repo = source.split("/", 1)
    return owner, repo


def get_default_branch(source: str, *, client: httpx.Client | None = None) -> str:
    """Return the repository default branch (cached)."""
    return _repo_info(source, client=client)[1]


def _git_blob_sha(content: bytes) -> str:
    """Return the git blob sha1 (`sha1("blob <len>\\0" + content)`).

    Identical to the blob sha GitHub exposes on trees/contents endpoints, so
    locally computed fingerprints stay comparable across runs and machines.
    """
    return hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest()


# Per-run cache: source -> (blobs, contents). Populated once per repo by the
# first tarball download; `get_skill_descriptions` reuses the cached contents
# so no additional API calls are made.
#   blobs:    {basename: (relative_path, blob_sha)}
#   contents: {relative_path: raw SKILL.md text}
_tarball_scan: dict[str, tuple[dict[str, tuple[str, str]], dict[str, str]]] = {}


def _parse_tarball(raw: bytes) -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    """Scan a repo tarball for every SKILL.md; return (blobs, contents).

    The tarball has a top-level `<repo>-<sha>/` directory, which is stripped so
    `relative_path` is the path within the repo (as used elsewhere).
    """
    blobs: dict[str, tuple[str, str]] = {}
    contents: dict[str, str] = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            parts = member.name.split("/", 1)
            if len(parts) < 2:
                continue  # the top-level directory entry itself
            rel = parts[1]
            if not rel.endswith("/SKILL.md"):
                continue
            f = tf.extractfile(member)
            if f is None:
                continue
            data = f.read()
            skill_dir = rel[: -len("/SKILL.md")]
            blobs[skill_dir.rsplit("/", 1)[-1]] = (skill_dir, _git_blob_sha(data))
            contents[skill_dir] = data.decode("utf-8", errors="replace")
    return blobs, contents


def _scan_repo(
    source: str, branch: str, *, client: httpx.Client
) -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    """Download (once) and scan a repo tarball; return (blobs, contents)."""
    cached = _tarball_scan.get(source)
    if cached is not None:
        return cached
    owner, repo = _split(source)
    url = f"{CODELOAD}/{owner}/{repo}/tar.gz/{quote(branch, safe='')}"
    resp = client.get(url)
    resp.raise_for_status()
    blobs, contents = _parse_tarball(resp.content)
    _tarball_scan[source] = (blobs, contents)
    return blobs, contents


def get_skill_blobs(  # noqa: E501
    source: str, branch: str = "HEAD", *, client: httpx.Client | None = None
) -> dict[str, tuple[str, str]]:
    """Return {basename: (relative_path, blob_sha)} for every SKILL.md.

    Backed by a single codeload tarball download (not billed to the REST quota);
    the blob sha is computed locally with git's exact algorithm, keeping the
    file-level incremental fingerprints identical to the previous tree-based
    approach.
    """
    client = client or new_github_client()
    blobs, _contents = _scan_repo(source, branch, client=client)
    return blobs


def get_skill_dirs(  # noqa: E501
    source: str, branch: str = "HEAD", *, client: httpx.Client | None = None
) -> dict[str, str]:
    """Return {basename: relative_path} of every dir containing SKILL.md."""
    blobs = get_skill_blobs(source, branch, client=client)
    return {name: path for name, (path, _sha) in blobs.items()}


def extract_description(markdown: str) -> str:
    """Return the `description` from a SKILL.md YAML frontmatter (empty if absent)."""
    text = markdown.lstrip("\ufeff").lstrip()
    if not text.startswith("---"):
        return ""
    body = text[3:].lstrip("\n")
    parts = body.split("\n---", 1)
    if len(parts) < 2:
        return ""
    try:
        data = yaml.safe_load(parts[0]) or {}
    except yaml.YAMLError:
        return ""
    desc = data.get("description")
    return str(desc).strip() if desc else ""


def get_skill_descriptions(  # noqa: E501
    source: str,
    blobs: dict[str, tuple[str, str]],
    *,
    client: httpx.Client | None = None,
) -> dict[str, str]:
    """Return {basename: description} from the already-downloaded tarball.

    `blobs` is a {basename: (relative_path, blob_sha)} subset -- typically just
    the blobs whose sha changed since the last scan. The tarball was already
    fetched by `get_skill_blobs` (cached in `_tarball_scan`), so this performs
    no network I/O; unchanged skills keep their old records via `scan`.
    """
    if not blobs:
        return {}
    client = client or new_github_client()
    _full_blobs, contents = _scan_repo(source, "HEAD", client=client)
    out: dict[str, str] = {}
    for name, (path, _sha) in blobs.items():
        out[name] = extract_description(contents.get(path, ""))
    return out


def get_repo_meta(source: str, *, client: httpx.Client | None = None) -> tuple[str, str, int]:
    """Return (pushed_at, default_branch, stars)."""
    return _repo_info(source, client=client)


def _is_missing_repo(exc: Exception) -> bool:
    """True if the failure chain contains a definitive 404 (repo not found).

    `get_json` wraps a 404 as ``HttpError from HTTPStatusError(404)``, so we
    walk the cause chain looking for that HTTP status.
    """
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, httpx.HTTPStatusError) and cur.response.status_code == 404:
            return True
        cur = cur.__cause__
    return False


def get_repo_metas(
    sources: list[str], *, client: httpx.Client | None = None, max_workers: int = 8
) -> tuple[dict[str, tuple[str, str, int]], set[str]]:
    """Concurrently fetch (pushed_at, default_branch, stars) for many repos.

    Returns ``(metas, missing)``:
    - `metas` maps the repos whose metadata was fetched successfully;
    - `missing` contains the sources whose repo is definitively gone (404,
      e.g. deleted or renamed), so callers can drop their stale data.

    Other failures are reported and skipped. Network-bound, so concurrent
    fetches materially cut wall-clock time on large source sets.
    """
    if not sources:
        return {}, set()
    client = client or new_github_client()

    def work(source: str) -> tuple[str, Exception | tuple[str, str, int]]:
        try:
            return source, _repo_info(source, client=client)
        except Exception as exc:  # noqa: BLE001
            return source, exc

    out: dict[str, tuple[str, str, int]] = {}
    missing: set[str] = set()
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for source, res in ex.map(work, list(sources)):
            if isinstance(res, Exception):
                if _is_missing_repo(res):
                    missing.add(source)
                    print(f"  [gone] {source}: repo not found (404)")
                else:
                    print(f"  [skip] {source}: meta fetch failed - {res}")
                continue
            out[source] = res
    return out, missing
