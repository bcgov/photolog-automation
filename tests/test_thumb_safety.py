"""Source-preservation guards for the thumbnail job.

The whole reason we use `gm mogrify -output-directory <dst>` (instead of the
default-in-place mogrify) is that the original source JPEGs must never be
modified. These tests exercise the `ThumbPlan.__post_init__` overlap asserts
and, when `gm` is available, run an end-to-end segment and verify source
bytes are untouched.
"""
from __future__ import annotations

import hashlib
import queue
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from photolog.core.thumb_detect import SegmentSpec
from photolog.core.thumb_job import ThumbJob, ThumbPlan


GM_PATH = shutil.which("gm")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _seed_segment(parent: Path, name: str, count: int = 3, gm: str | None = None) -> SegmentSpec:
    """Seed a segment folder with `count` JPEGs. If gm is available we use it
    to generate real 640x480 solid-color JPEGs — guaranteed decodable. Without
    gm the test that uses this is skipped, so byte contents don't matter."""
    seg = parent / name
    seg.mkdir(parents=True)
    total = 0
    for i in range(count):
        dst = seg / f"img{i:04d}.jpg"
        if gm:
            subprocess.run(
                [gm, "convert", "-size", "640x480", f"xc:rgb({i * 20 % 255},100,200)",
                 "-quality", "90", str(dst)],
                check=True, capture_output=True,
            )
        else:
            dst.write_bytes(b"\x00" * 1024)
        total += dst.stat().st_size
    return SegmentSpec(name=name, source_folder=seg, image_count=count, bytes_total=total)


def test_plan_refuses_src_eq_dst(tmp_path: Path):
    seg = _seed_segment(tmp_path, "H1_E_00_km0000", count=1)
    with pytest.raises(ValueError):
        ThumbPlan(
            year=2023,
            segments=[seg],
            dest_folder=tmp_path,  # dst/<seg.name> == src
            gm_exe=Path("/bin/true"),
        )


def test_plan_refuses_src_inside_dst(tmp_path: Path):
    src_root = tmp_path / "src"
    src_root.mkdir()
    seg = _seed_segment(src_root, "H1_E_00_km0000", count=1)
    # Dest = parent of src — overlap detected both ways
    with pytest.raises(ValueError):
        ThumbPlan(
            year=2023,
            segments=[seg],
            dest_folder=src_root,
            gm_exe=Path("/bin/true"),
        )


@pytest.mark.skipif(GM_PATH is None, reason="gm not installed; skipping e2e safety test")
def test_sources_untouched_after_mogrify(tmp_path: Path):
    src_root = tmp_path / "hr"
    dest_root = tmp_path / "tn"
    seg = _seed_segment(src_root, "H1_E_00_km0000", count=2, gm=GM_PATH)

    before = {p.name: _sha(p) for p in seg.source_folder.iterdir() if p.suffix.lower() == ".jpg"}

    plan = ThumbPlan(
        year=2023,
        segments=[seg],
        dest_folder=dest_root,
        gm_exe=Path(GM_PATH),
        skip_existing=False,
        workers=1,
    )
    q: "queue.Queue" = queue.Queue()
    cancel = threading.Event()
    ThumbJob(plan, q, cancel).run()

    after = {p.name: _sha(p) for p in seg.source_folder.iterdir() if p.suffix.lower() == ".jpg"}
    assert before == after, "mogrify must not modify source files"

    for name in before:
        thumb = dest_root / seg.name / name
        assert thumb.exists() and thumb.stat().st_size > 0, f"missing thumbnail for {name}"
