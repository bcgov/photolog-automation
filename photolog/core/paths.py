"""Drive-root resolution and year detection.

Resolution order for every path:
    env var override  ->  config file (per hostname)  ->  auto-detect default

Env vars (primarily for the Mac dev loop):
    PHOTOLOG_HR      -> HR root          (equiv. N:\\RPM on Windows)
    PHOTOLOG_TN      -> TN root          (equiv. N:\\RPM\\TN)
    PHOTOLOG_HR_LINK -> H:\\PLGwww\\hr equivalent
    PHOTOLOG_TN_LINK -> H:\\PLGwww\\TN equivalent
    PHOTOLOG_GM      -> gm executable

Legacy env vars PHOTOLOG_N / PHOTOLOG_H still work and, if set, are expanded:
    PHOTOLOG_N  ->  hr_root = <N>,    tn_root = <N>/TN
    PHOTOLOG_H  ->  hr_link = <H>/hr, tn_link = <H>/TN
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from photolog.core.config import AppConfig, MachineConfig, load_config

IS_WINDOWS = sys.platform.startswith("win")

_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")
_HIGHWAY_IMAGES_RE = re.compile(
    r"^(?P<year>(?:19|20)\d{2})\s+Highway\s+Images\s*$", re.IGNORECASE
)


# ---------------------------------------------------------------------------
# Effective machine config (env -> config file -> auto-detect)
# ---------------------------------------------------------------------------

def _auto_detect_hr_root() -> Path | None:
    for candidate in (r"N:\RPM", r"N:\RPMS$", "N:\\"):
        p = Path(candidate)
        if p.exists():
            return p
    if not IS_WINDOWS:
        # Dev-loop fallback
        p = Path.home() / "photolog-sim" / "N"
        if p.exists():
            return p
    return None


def _auto_detect_hr_link_root() -> Path | None:
    for candidate in (r"H:\PLGwww", "H:\\"):
        p = Path(candidate)
        if p.exists():
            return p
    if not IS_WINDOWS:
        p = Path.home() / "photolog-sim" / "H"
        if p.exists():
            return p
    return None


def _auto_detect_gm(hr_root: Path | None) -> Path | None:
    if hr_root is not None:
        candidate = hr_root / "TN" / "GraphicsMagick-1.3.23-Q8" / ("gm.exe" if IS_WINDOWS else "gm")
        if candidate.exists():
            return candidate
    if not IS_WINDOWS:
        for candidate in ("/opt/homebrew/bin/gm", "/usr/local/bin/gm", "/usr/bin/gm"):
            if Path(candidate).exists():
                return Path(candidate)
    return None


def effective_config() -> MachineConfig:
    """Resolve the config the app should use right now, env > file > detect."""
    cfg = load_config().for_current_machine()

    hr_root = os.environ.get("PHOTOLOG_HR") or os.environ.get("PHOTOLOG_N") or cfg.hr_root
    tn_root = os.environ.get("PHOTOLOG_TN") or cfg.tn_root
    hr_link = os.environ.get("PHOTOLOG_HR_LINK") or cfg.hr_link_root
    tn_link = os.environ.get("PHOTOLOG_TN_LINK") or cfg.tn_link_root
    gm_exe = os.environ.get("PHOTOLOG_GM") or cfg.gm_exe

    # Legacy PHOTOLOG_H expands to hr_link/tn_link
    legacy_h = os.environ.get("PHOTOLOG_H")
    if legacy_h:
        if not hr_link:
            hr_link = str(Path(legacy_h) / "hr")
        if not tn_link:
            tn_link = str(Path(legacy_h) / "TN")

    # Auto-detect anything still missing
    if not hr_root:
        d = _auto_detect_hr_root()
        if d is not None:
            hr_root = str(d)
    if not tn_root and hr_root:
        tn_root = str(Path(hr_root) / "TN")
    if not hr_link:
        d = _auto_detect_hr_link_root()
        if d is not None:
            hr_link = str(d / "hr")
    if not tn_link:
        d = _auto_detect_hr_link_root()
        if d is not None:
            tn_link = str(d / "TN")
    if not gm_exe:
        d = _auto_detect_gm(Path(hr_root) if hr_root else None)
        if d is not None:
            gm_exe = str(d)

    return MachineConfig(
        hr_root=hr_root or "",
        tn_root=tn_root or "",
        hr_link_root=hr_link or "",
        tn_link_root=tn_link or "",
        gm_exe=gm_exe or "",
    )


def is_configured() -> bool:
    """True iff all required paths resolve to something non-empty."""
    return effective_config().is_complete()


# ---------------------------------------------------------------------------
# Public path accessors (used everywhere else)
# ---------------------------------------------------------------------------

def hr_root() -> Path:
    cfg = effective_config()
    if not cfg.hr_root:
        raise RuntimeError("HR root is not configured. Open Settings to set it.")
    return Path(cfg.hr_root)


def tn_root() -> Path:
    cfg = effective_config()
    if not cfg.tn_root:
        raise RuntimeError("TN root is not configured. Open Settings to set it.")
    return Path(cfg.tn_root)


def hr_link_root() -> Path:
    cfg = effective_config()
    if not cfg.hr_link_root:
        raise RuntimeError("HR junction root is not configured. Open Settings to set it.")
    return Path(cfg.hr_link_root)


def tn_link_root() -> Path:
    cfg = effective_config()
    if not cfg.tn_link_root:
        raise RuntimeError("TN junction root is not configured. Open Settings to set it.")
    return Path(cfg.tn_link_root)


def gm_executable() -> Path:
    cfg = effective_config()
    if not cfg.gm_exe:
        raise RuntimeError("gm.exe path is not configured. Open Settings to set it.")
    return Path(cfg.gm_exe)


def year_dest_root(year: int) -> Path:
    return hr_root() / str(year)


def year_tn_root(year: int) -> Path:
    return tn_root() / str(year)


# ---------------------------------------------------------------------------
# Year detection helpers (unchanged API, re-rooted on new hr_root())
# ---------------------------------------------------------------------------

def detect_years_on_drive() -> list[int]:
    """Year folders already present under the HR root."""
    try:
        root = hr_root()
    except RuntimeError:
        return []
    if not root.exists():
        return []
    years: set[int] = set()
    for child in root.iterdir():
        if child.is_dir() and _YEAR_RE.fullmatch(child.name):
            years.add(int(child.name))
    return sorted(years)


def detect_year_from_source(source: Path) -> int | None:
    """Scan a source folder for a '<year> Highway Images' subfolder, or a top-level <YYYY>."""
    if not source.exists() or not source.is_dir():
        return None
    for child in source.iterdir():
        if not child.is_dir():
            continue
        m = _HIGHWAY_IMAGES_RE.match(child.name)
        if m:
            return int(m.group("year"))
    for child in source.iterdir():
        if child.is_dir() and _YEAR_RE.fullmatch(child.name):
            return int(child.name)
    return None


def find_highway_images_folder(year_root: Path, year: int) -> Path | None:
    """Locate '<year> Highway Images' under a given year dir, tolerant to casing."""
    if not year_root.exists():
        return None
    exact = year_root / f"{year} Highway Images"
    if exact.is_dir():
        return exact
    for child in year_root.iterdir():
        if not child.is_dir():
            continue
        m = _HIGHWAY_IMAGES_RE.match(child.name)
        if m and int(m.group("year")) == year:
            return child
    return None
