"""Table-driven tests for copy_detect + thumb_detect.

The detectors are pure functions with a small, enumerable set of input shapes.
Any change in heuristics should update one table row, not hunt for implicit
behaviour in the UI tabs.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from photolog.core.copy_detect import CopyInterpretation, Refusal as CopyRefusal, interpret_copy_source
from photolog.core.thumb_detect import (
    Refusal as ThumbRefusal,
    ThumbInterpretation,
    interpret_thumb_source,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _touch(path: Path, nbytes: int = 16) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * nbytes)


def _make_segment(parent: Path, name: str, count: int = 3) -> Path:
    seg = parent / name
    seg.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        _touch(seg / f"img{i:04d}.jpg")
    return seg


# --------------------------------------------------------------------------- #
# Copy detector
# --------------------------------------------------------------------------- #

def test_copy_year_container(tmp_path: Path):
    year_dir = tmp_path / "2023"
    hw = year_dir / "2023 Highway Images"
    _make_segment(hw, "H1_E_00_km0000")
    _make_segment(hw, "H1_E_00_km0100")
    r = interpret_copy_source(tmp_path)
    assert isinstance(r, CopyInterpretation)
    assert r.shape == "year_container"
    assert r.year == 2023
    assert r.segment_count == 2


def test_copy_year_contents(tmp_path: Path):
    hw = tmp_path / "2023 Highway Images"
    _make_segment(hw, "H1_E_00_km0000")
    _touch(tmp_path / "survey.shp")
    r = interpret_copy_source(tmp_path)
    assert isinstance(r, CopyInterpretation)
    assert r.shape == "year_contents"
    assert r.year == 2023
    assert r.segment_count == 1


def test_copy_bare_dump(tmp_path: Path):
    (tmp_path / "random_folder").mkdir()
    _touch(tmp_path / "readme.txt")
    r = interpret_copy_source(tmp_path)
    assert isinstance(r, CopyInterpretation)
    assert r.shape == "bare_dump"
    assert r.year is None


def test_copy_refuses_file(tmp_path: Path):
    f = tmp_path / "a.jpg"
    _touch(f)
    r = interpret_copy_source(f)
    assert isinstance(r, CopyRefusal)


def test_copy_refuses_missing(tmp_path: Path):
    r = interpret_copy_source(tmp_path / "nope")
    assert isinstance(r, CopyRefusal)


def test_copy_refuses_empty(tmp_path: Path):
    r = interpret_copy_source(tmp_path)
    assert isinstance(r, CopyRefusal)


# --------------------------------------------------------------------------- #
# Thumbnail detector
# --------------------------------------------------------------------------- #

def test_thumb_highway_images(tmp_path: Path):
    hw = tmp_path / "2023 Highway Images"
    _make_segment(hw, "H1_E_00_km0000", count=3)
    _make_segment(hw, "H1_E_00_km0100", count=2)
    r = interpret_thumb_source(hw)
    assert isinstance(r, ThumbInterpretation)
    assert r.shape == "highway_images"
    assert len(r.segments) == 2
    assert r.total_images == 5


def test_thumb_year_folder_descends(tmp_path: Path):
    year = tmp_path / "2023"
    hw = year / "2023 Highway Images"
    _make_segment(hw, "H1_E_00_km0000", count=4)
    r = interpret_thumb_source(year)
    assert isinstance(r, ThumbInterpretation)
    assert r.shape == "highway_images"
    assert len(r.segments) == 1
    assert r.total_images == 4


def test_thumb_single_segment(tmp_path: Path):
    seg = tmp_path / "H1_E_00_km0000"
    seg.mkdir()
    for i in range(3):
        _touch(seg / f"img{i}.jpg")
    r = interpret_thumb_source(seg)
    assert isinstance(r, ThumbInterpretation)
    assert r.shape in ("single_segment", "loose_images")
    assert len(r.segments) == 1
    assert r.segments[0].name == "H1_E_00_km0000"


def test_thumb_refuses_nested_too_deep(tmp_path: Path):
    # Picked folder has subfolders that themselves contain segment subfolders —
    # user picked too high. Detector must bail out.
    deep = tmp_path / "Surveys" / "2023" / "H1_E_00"
    deep.mkdir(parents=True)
    _touch(deep / "img.jpg")
    r = interpret_thumb_source(tmp_path)
    assert isinstance(r, ThumbRefusal)


def test_thumb_refuses_file(tmp_path: Path):
    f = tmp_path / "a.jpg"
    _touch(f)
    r = interpret_thumb_source(f)
    assert isinstance(r, ThumbRefusal)


def test_thumb_skips_empty_segments(tmp_path: Path):
    hw = tmp_path / "2023 Highway Images"
    _make_segment(hw, "H1_E_00_km0000", count=2)
    (hw / "empty_seg").mkdir()
    r = interpret_thumb_source(hw)
    assert isinstance(r, ThumbInterpretation)
    assert [s.name for s in r.segments] == ["H1_E_00_km0000"]
    assert any("empty" in n for n in r.notes)
