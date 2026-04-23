"""Directory junction (mklink /J) creation + detection.

On Windows we shell out to `cmd /c mklink /J`. Junctions don't require admin
and are the right primitive for web-server-visible drive aliases.
On macOS (dev loop) we emulate with symlinks so the same code paths exercise.
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from photolog.core.paths import IS_WINDOWS

FILE_ATTRIBUTE_REPARSE_POINT = 0x400


@dataclass
class JunctionInfo:
    exists: bool
    target: Path | None
    is_reparse: bool


def inspect(link: Path) -> JunctionInfo:
    if not link.exists() and not link.is_symlink():
        return JunctionInfo(exists=False, target=None, is_reparse=False)
    is_reparse = False
    if IS_WINDOWS:
        import ctypes

        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(link))
        if attrs != 0xFFFFFFFF:
            is_reparse = bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)
    else:
        is_reparse = link.is_symlink()
    target: Path | None = None
    try:
        target = Path(os.readlink(str(link)))
    except OSError:
        target = None
    return JunctionInfo(exists=True, target=target, is_reparse=is_reparse)


class JunctionConflict(RuntimeError):
    pass


def ensure_junction(link: Path, target: Path) -> str:
    """Create junction if missing. If present and pointing at target, no-op.
    If pointing elsewhere, raise JunctionConflict.

    Returns one of: "created", "exists-ok".
    """
    target = target.resolve(strict=False) if target.exists() else target
    info = inspect(link)
    if info.exists:
        if info.is_reparse and info.target is not None and _same_path(info.target, target):
            return "exists-ok"
        raise JunctionConflict(
            f"{link} already exists (reparse={info.is_reparse}, target={info.target}); refusing to re-point."
        )
    link.parent.mkdir(parents=True, exist_ok=True)
    if IS_WINDOWS:
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"mklink /J failed ({completed.returncode}): {completed.stderr.strip() or completed.stdout.strip()}"
            )
    else:
        os.symlink(str(target), str(link), target_is_directory=True)
    return "created"


def _same_path(a: Path, b: Path) -> bool:
    try:
        return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(
            os.path.normpath(str(b))
        )
    except Exception:
        return False
