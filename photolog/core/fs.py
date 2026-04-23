"""Filesystem helpers: hashing, disk usage, equality checks."""
from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path

HASH_CHUNK = 1 << 20  # 1 MiB
COPY_CHUNK = 4 << 20  # 4 MiB
MTIME_TOL_S = 2.0  # FAT/SMB mtime granularity


def sha256_file(path: Path, cancel=None) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            if cancel is not None and cancel.is_set():
                raise InterruptedError("hash cancelled")
            chunk = f.read(HASH_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def files_look_equal(src: Path, dst: Path) -> bool:
    """Cheap equality test: size and mtime (within tolerance)."""
    try:
        ss = src.stat()
        ds = dst.stat()
    except OSError:
        return False
    return ss.st_size == ds.st_size and abs(ss.st_mtime - ds.st_mtime) <= MTIME_TOL_S


@dataclass
class DiskStats:
    path: Path
    total: int
    used: int
    free: int
    exists: bool

    @classmethod
    def for_path(cls, path: Path) -> "DiskStats":
        if not path.exists():
            return cls(path=path, total=0, used=0, free=0, exists=False)
        u = shutil.disk_usage(str(path))
        return cls(path=path, total=u.total, used=u.used, free=u.free, exists=True)

    @classmethod
    def missing(cls) -> "DiskStats":
        return cls(path=Path(""), total=0, used=0, free=0, exists=False)


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(n) < 1024.0:
            return f"{n:3.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} EB"


def human_duration(seconds: float) -> str:
    if seconds < 0 or seconds != seconds:  # NaN
        return "--:--"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"
