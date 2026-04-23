"""Atomic JSON manifest read/write with schema versioning."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


def read_manifest(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    data.setdefault("version", SCHEMA_VERSION)
    return data


def write_manifest(path: Path, data: dict[str, Any]) -> None:
    """Atomic write: tmp in same dir, fsync, os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {**data, "version": SCHEMA_VERSION}
    fd, tmp_path = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
