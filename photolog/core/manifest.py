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
    """Atomic write: tmp in same dir, fsync, os.replace with retry.

    Retries os.replace() up to 3 times on Windows-specific lock errors
    (antivirus scanners, file locking). If all retries exhaust, the
    exception is re-raised with context.
    """
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
        # Retry os.replace on transient lock failures (Windows antivirus, etc.)
        last_error = None
        for attempt in range(3):
            try:
                os.replace(tmp_path, path)
                return
            except (FileExistsError, PermissionError) as e:
                last_error = e
                if attempt < 2:
                    import time
                    time.sleep(0.1 * (attempt + 1))
                    continue
                raise RuntimeError(
                    f"Failed to write manifest {path} after 3 attempts: {last_error}"
                ) from last_error
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
