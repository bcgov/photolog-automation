"""Interpret a user-picked source folder for the Copy tab.

Returns a CopyInterpretation describing what we think the user picked,
or a Refusal explaining why we can't run against it. The UI passes this
to the PreflightCard and only starts a job once the user confirms.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Union

_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")
_HIGHWAY_IMAGES_RE = re.compile(r"^(?P<year>(?:19|20)\d{2})\s+Highway\s+Images\s*$", re.IGNORECASE)
_GEODATA_EXTS = {".shp", ".shx", ".dbf", ".prj", ".csv"}

CopyShape = Literal["year_container", "year_contents", "bare_dump"]


@dataclass
class CopyInterpretation:
    shape: CopyShape
    source_root: Path       # the folder whose contents we'll copy verbatim
    year: int | None        # may be None for bare_dump — user must supply
    segment_count: int = 0  # immediate subfolders under <year> Highway Images, if found
    notes: list[str] = field(default_factory=list)


@dataclass
class Refusal:
    reason: str


Result = Union[CopyInterpretation, Refusal]


def interpret_copy_source(path: Path) -> Result:
    if not path.exists():
        return Refusal(f"{path} does not exist.")
    if not path.is_dir():
        return Refusal(f"{path} is a file — pick a folder.")
    try:
        resolved = path.resolve()
    except OSError as e:
        return Refusal(f"Can't resolve {path}: {e}")
    if resolved.parent == resolved:
        return Refusal("Can't use a drive root directly. Pick the folder that contains your year.")

    children = _safe_listdir(path)
    if children is None:
        return Refusal(f"Can't read {path}.")
    if not children:
        return Refusal(f"{path} is empty.")

    year_subdirs = [c for c in children if c.is_dir() and _YEAR_RE.fullmatch(c.name)]
    highway_here = [c for c in children if c.is_dir() and _HIGHWAY_IMAGES_RE.match(c.name)]
    has_geodata_here = any(c.is_file() and c.suffix.lower() in _GEODATA_EXTS for c in children)

    # Shape A: year container — picked folder has exactly one <YYYY>/ subfolder
    if len(year_subdirs) == 1 and not highway_here:
        year_folder = year_subdirs[0]
        year = int(year_folder.name)
        nested_children = _safe_listdir(year_folder) or []
        nested_highway = [c for c in nested_children if c.is_dir() and _HIGHWAY_IMAGES_RE.match(c.name)]
        segments = _count_segments(nested_highway[0]) if nested_highway else 0
        notes: list[str] = []
        if not nested_highway:
            notes.append(f"No '{year} Highway Images' folder found inside {year_folder.name}.")
        return CopyInterpretation(
            shape="year_container",
            source_root=year_folder,
            year=year,
            segment_count=segments,
            notes=notes,
        )

    # Shape B: year contents — picked folder itself is the year payload
    if highway_here:
        year = int(_HIGHWAY_IMAGES_RE.match(highway_here[0].name).group("year"))
        segments = _count_segments(highway_here[0])
        notes = []
        if not has_geodata_here:
            notes.append("No .shp/.dbf/.csv siblings found — unusual for a year payload, but not blocked.")
        return CopyInterpretation(
            shape="year_contents",
            source_root=path,
            year=year,
            segment_count=segments,
            notes=notes,
        )

    # Shape C: bare dump — couldn't identify year, defer to user
    return CopyInterpretation(
        shape="bare_dump",
        source_root=path,
        year=None,
        segment_count=0,
        notes=["Couldn't detect a year from folder structure. Enter the year manually."],
    )


def _safe_listdir(p: Path) -> list[Path] | None:
    try:
        return list(p.iterdir())
    except OSError:
        return None


def _count_segments(highway_folder: Path) -> int:
    children = _safe_listdir(highway_folder) or []
    return sum(1 for c in children if c.is_dir())
