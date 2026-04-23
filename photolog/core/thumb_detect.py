"""Interpret a user-picked source folder for the Thumbnail tab.

The contract: whatever shape we accept, the output always lands at
<TN root>/<year>/<segment>/<image>. A "segment" is the immediate parent
folder of the image files. Year is supplied separately by the user and
NEVER inferred from the folder name alone — that would let a source
typo quietly write to the wrong destination.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Union

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
_HIGHWAY_IMAGES_RE = re.compile(r"^(?P<year>(?:19|20)\d{2})\s+Highway\s+Images\s*$", re.IGNORECASE)
_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")

ThumbShape = Literal["highway_images", "year_folder", "single_segment", "loose_images"]


@dataclass
class SegmentSpec:
    name: str             # used as the TN subfolder name
    source_folder: Path   # where the source JPEGs live
    image_count: int
    bytes_total: int


@dataclass
class ThumbInterpretation:
    shape: ThumbShape
    source_root: Path            # what we'll pass to gm for each segment
    segments: list[SegmentSpec]
    total_images: int
    total_bytes: int
    notes: list[str] = field(default_factory=list)


@dataclass
class Refusal:
    reason: str


Result = Union[ThumbInterpretation, Refusal]


def interpret_thumb_source(path: Path) -> Result:
    if not path.exists():
        return Refusal(f"{path} does not exist.")
    if not path.is_dir():
        return Refusal(f"{path} is a file — pick a folder.")
    try:
        resolved = path.resolve()
    except OSError as e:
        return Refusal(f"Can't resolve {path}: {e}")
    if resolved.parent == resolved:
        return Refusal("Can't use a drive root directly. Pick the '<year> Highway Images' folder or a segment.")

    children = _safe_listdir(path)
    if children is None:
        return Refusal(f"Can't read {path}.")
    if not children:
        return Refusal(f"{path} is empty.")

    subfolders = [c for c in children if c.is_dir()]
    direct_images = [c for c in children if c.is_file() and c.suffix.lower() in IMAGE_EXTS]

    # Shape A: <year> Highway Images — subfolders are segments
    if _HIGHWAY_IMAGES_RE.match(path.name) and subfolders:
        return _interpret_highway_images(path, subfolders)

    # Shape B: <year>/ — descend into its Highway Images subfolder
    if _YEAR_RE.fullmatch(path.name):
        hw = [c for c in subfolders if _HIGHWAY_IMAGES_RE.match(c.name)]
        if len(hw) == 1:
            inner = _safe_listdir(hw[0])
            inner_subs = [c for c in (inner or []) if c.is_dir()]
            if inner_subs:
                interp = _interpret_highway_images(hw[0], inner_subs)
                if isinstance(interp, ThumbInterpretation):
                    interp.notes.insert(0, f"Auto-descended into '{hw[0].name}'.")
                return interp
        return Refusal(
            f"'{path.name}' looks like a year folder, but no usable "
            f"'<year> Highway Images' with segment subfolders was found."
        )

    # Shape C: looks like a single segment — images directly under picked folder, no image subfolders
    if direct_images and not any(_folder_has_images(c) for c in subfolders):
        seg = SegmentSpec(
            name=path.name,
            source_folder=path,
            image_count=len(direct_images),
            bytes_total=_sum_sizes(direct_images),
        )
        return ThumbInterpretation(
            shape="single_segment" if subfolders else "loose_images",
            source_root=path,
            segments=[seg],
            total_images=seg.image_count,
            total_bytes=seg.bytes_total,
            notes=[
                f"Treating '{path.name}' as a single segment. "
                "Thumbnails will land at <TN>/<year>/" + path.name + "/."
            ],
        )

    # Shape D: generic folder with subfolders containing images
    image_subs = [c for c in subfolders if _folder_has_images(c)]
    if image_subs:
        if any(_folder_has_images_recursive(c, depth=2) for c in subfolders):
            # Detect nested structure deeper than one level — user likely picked too high
            return Refusal(
                "Source has image folders nested more than one level deep. "
                "Pick either the '<year> Highway Images' folder or a single segment."
            )
        return _interpret_highway_images(path, subfolders)

    return Refusal(
        "Source contains no images. "
        "Pick the '<year> Highway Images' folder or a segment folder (e.g. H1_E_00_km0000)."
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _interpret_highway_images(src: Path, subfolders: list[Path]) -> Result:
    segments: list[SegmentSpec] = []
    total_images = 0
    total_bytes = 0
    empty_segments: list[str] = []
    for sf in sorted(subfolders, key=lambda p: p.name):
        images = [c for c in (_safe_listdir(sf) or []) if c.is_file() and c.suffix.lower() in IMAGE_EXTS]
        if not images:
            empty_segments.append(sf.name)
            continue
        size = _sum_sizes(images)
        segments.append(SegmentSpec(
            name=sf.name,
            source_folder=sf,
            image_count=len(images),
            bytes_total=size,
        ))
        total_images += len(images)
        total_bytes += size
    if not segments:
        return Refusal(
            f"No segment subfolders of '{src.name}' contain any images."
        )
    notes: list[str] = []
    if empty_segments:
        notes.append(f"{len(empty_segments)} empty segment folder(s) will be skipped.")
    return ThumbInterpretation(
        shape="highway_images",
        source_root=src,
        segments=segments,
        total_images=total_images,
        total_bytes=total_bytes,
        notes=notes,
    )


def _folder_has_images(p: Path) -> bool:
    try:
        for entry in os.scandir(p):
            if entry.is_file() and os.path.splitext(entry.name)[1].lower() in IMAGE_EXTS:
                return True
    except OSError:
        return False
    return False


def _folder_has_images_recursive(p: Path, depth: int) -> bool:
    """Return True if an image exists deeper than `depth` levels below p."""
    if depth <= 0:
        return _folder_has_images(p)
    try:
        for entry in os.scandir(p):
            if entry.is_dir(follow_symlinks=False):
                if _folder_has_images_recursive(Path(entry.path), depth - 1):
                    return True
    except OSError:
        return False
    return False


def _safe_listdir(p: Path) -> list[Path] | None:
    try:
        return list(p.iterdir())
    except OSError:
        return None


def _sum_sizes(paths: list[Path]) -> int:
    total = 0
    for p in paths:
        try:
            total += p.stat().st_size
        except OSError:
            pass
    return total
