"""JSON / JSONL persistence helpers."""

from __future__ import annotations

import json
from pathlib import Path

from .config import JSON, Record


def write_jsonl(path: Path, records: list[Record]) -> None:
    """Overwrite `path` with one JSON object per line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[Record]:
    """Read a JSONL file, skipping blank lines. Missing file -> empty list."""
    if not path.exists():
        return []
    out: list[Record] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def write_json(path: Path, data: Record) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def read_json(path: Path, default: JSON | None = None) -> JSON | None:
    """Read a JSON file. Missing or unparseable file -> `default`."""
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default
