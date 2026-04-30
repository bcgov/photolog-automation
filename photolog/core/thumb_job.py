"""Parallel thumbnail generation via GraphicsMagick mogrify.

One `gm.exe mogrify` invocation per segment folder — ~245 invocations for a
typical year instead of ~50k for per-file convert. `-output-directory`
guarantees the source files are never overwritten (gm opens them read-only
when outputting elsewhere).

The segment list is supplied by `core.thumb_detect.interpret_thumb_source`
so the job never decides what counts as a segment on its own.
"""
from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, Future, wait, FIRST_COMPLETED
from dataclasses import dataclass, field
from pathlib import Path

from photolog.core import dev, manifest
from photolog.core.events import ProgressEvent
from photolog.core.fs import NO_WINDOW_FLAGS
from photolog.core.thumb_detect import IMAGE_EXTS, SegmentSpec

MANIFEST_NAME = ".photolog-thumb-manifest.json"
THROUGHPUT_WINDOW_S = 30.0
THUMB_SIZE = "320x254"
THUMB_QUALITY = "75"
DEFAULT_WORKERS = 2  # HDD-friendly default; more workers cause seek thrashing


@dataclass
class ThumbPlan:
    year: int
    segments: list[SegmentSpec]
    dest_folder: Path           # e.g. <TN root>/2023
    gm_exe: Path
    skip_existing: bool = True
    workers: int = DEFAULT_WORKERS
    force_regenerate: bool = False

    def __post_init__(self) -> None:
        for seg in self.segments:
            src = seg.source_folder.resolve(strict=False)
            dst = (self.dest_folder / seg.name).resolve(strict=False)
            if src == dst:
                raise ValueError(f"Segment '{seg.name}' source and destination resolve to the same path: {src}")
            try:
                if dst.is_relative_to(src) or src.is_relative_to(dst):
                    raise ValueError(
                        f"Segment '{seg.name}' destination {dst} overlaps source {src}. "
                        f"Refusing to run — would risk overwriting originals."
                    )
            except AttributeError:
                # Python < 3.9 — str-based fallback
                if str(dst).startswith(str(src) + os.sep) or str(src).startswith(str(dst) + os.sep):
                    raise ValueError(f"Segment '{seg.name}' destination overlaps source.")


@dataclass
class _SegmentResult:
    name: str
    ok: bool
    generated: int = 0
    skipped: int = 0
    failed: int = 0
    error: str = ""


class ThumbJob:
    def __init__(
        self,
        plan: ThumbPlan,
        out_queue: "queue.Queue[ProgressEvent]",
        cancel: threading.Event,
    ):
        self.plan = plan
        self.q = out_queue
        self.cancel = cancel
        self._segments_total = len(plan.segments)
        self._segments_done = 0
        self._files_total = sum(s.image_count for s in plan.segments)
        self._files_done = 0
        self._files_skipped = 0
        self._files_failed = 0
        self._failures: list[dict] = []
        self._samples: list[tuple[float, int]] = []

    def run(self) -> None:
        try:
            if not self.plan.gm_exe.exists():
                self._emit("error", f"GraphicsMagick not found at {self.plan.gm_exe}")
                return
            self.plan.dest_folder.mkdir(parents=True, exist_ok=True)
            self._emit(
                "copying",
                f"Generating thumbnails for {self._segments_total} segment(s) / "
                f"{self._files_total} image(s)…",
            )
            self._run_parallel()
            self._write_manifest()
            if self.cancel.is_set():
                self._emit("cancelled", "Cancelled.")
                return
            if self._files_failed:
                self._emit("error", f"Completed with {self._files_failed} failure(s).")
            else:
                self._emit("done", "Thumbnails complete.")
        except Exception as e:  # noqa: BLE001
            self._emit("error", f"Thumbnail job failed: {e}")

    # ---------- execution ----------

    def _run_parallel(self) -> None:
        self._samples.append((time.monotonic(), 0))
        if not self.plan.segments:
            return
        with ThreadPoolExecutor(max_workers=max(1, self.plan.workers)) as ex:
            futures: dict[Future, SegmentSpec] = {}
            it = iter(self.plan.segments)
            for _ in range(min(self.plan.workers * 2, self._segments_total)):
                try:
                    seg = next(it)
                except StopIteration:
                    break
                futures[ex.submit(self._mogrify_segment, seg)] = seg
            while futures:
                if self.cancel.is_set():
                    for fut in futures:
                        fut.cancel()
                    break
                done, _ = wait(list(futures.keys()), timeout=0.5, return_when=FIRST_COMPLETED)
                for fut in done:
                    seg = futures.pop(fut)
                    result: _SegmentResult = fut.result()
                    self._segments_done += 1
                    self._files_done += result.generated
                    self._files_skipped += result.skipped
                    self._files_failed += result.failed
                    if not result.ok:
                        self._failures.append({"segment": seg.name, "error": result.error})
                    self._emit_progress(
                        f"{seg.name}: +{result.generated} new, "
                        f"{result.skipped} skipped, {result.failed} failed"
                    )
                    try:
                        nseg = next(it)
                        futures[ex.submit(self._mogrify_segment, nseg)] = nseg
                    except StopIteration:
                        pass

    def _mogrify_segment(self, seg: SegmentSpec) -> _SegmentResult:
        if self.cancel.is_set():
            return _SegmentResult(name=seg.name, ok=False, error="cancelled")
        # Dev throttle — sleep BEFORE work so cancel stays responsive.
        delay = dev.SETTINGS.thumb_segment_delay_s
        if delay > 0:
            end = time.monotonic() + delay
            while time.monotonic() < end:
                if self.cancel.is_set():
                    return _SegmentResult(name=seg.name, ok=False, error="cancelled")
                time.sleep(min(0.1, end - time.monotonic()))
        # Dev forced-failure — bail without ever spawning gm.
        if dev.SETTINGS.fail_segment_name and seg.name == dev.SETTINGS.fail_segment_name:
            return _SegmentResult(name=seg.name, ok=False, error="dev: forced failure")

        dst_dir = self.plan.dest_folder / seg.name
        dst_dir.mkdir(parents=True, exist_ok=True)

        # Belt-and-suspenders — never let mogrify write into its own source.
        if dst_dir.resolve(strict=False) == seg.source_folder.resolve(strict=False):
            return _SegmentResult(name=seg.name, ok=False, error=f"dst == src for {seg.name}")

        images = _list_images(seg.source_folder)
        if not images:
            return _SegmentResult(name=seg.name, ok=True)

        # Skip-existing policy: if every thumb already exists and is newer than the source,
        # we don't need to run mogrify at all.
        pending: list[Path] = []
        skipped = 0
        if self.plan.skip_existing and not self.plan.force_regenerate:
            for img in images:
                thumb = dst_dir / img.name
                if _thumb_is_fresh(thumb, img):
                    skipped += 1
                else:
                    pending.append(img)
        else:
            pending = images

        if not pending:
            return _SegmentResult(name=seg.name, ok=True, skipped=skipped)

        # `gm mogrify -output-directory` builds each output path by prefixing
        # the directory onto the *input path as given on the command line*.
        # So we MUST pass bare filenames (and set cwd to the segment folder)
        # — absolute inputs would produce `<dst>/<abs src path>`.
        # We always enumerate filenames explicitly: gm.exe on Windows does not
        # expand `*.jpg` itself (the Unix build does, which is why glob worked
        # on Mac and failed on the Windows VM with "Unable to open file (*.jpg)").
        cmd_base: list[str] = [
            str(self.plan.gm_exe),
            "mogrify",
            "-output-directory",
            str(dst_dir.resolve(strict=False)),
            "-resize",
            THUMB_SIZE,
            "-quality",
            THUMB_QUALITY,
        ]

        for batch in _batch_filenames([p.name for p in pending], cmd_base):
            cmd = cmd_base + batch
            try:
                completed = subprocess.run(
                    cmd,
                    cwd=str(seg.source_folder),
                    capture_output=True,
                    text=True,
                    timeout=3600,
                    creationflags=NO_WINDOW_FLAGS,
                )
            except subprocess.TimeoutExpired:
                return _SegmentResult(name=seg.name, ok=False, skipped=skipped, failed=len(pending), error="gm timeout")
            except OSError as e:
                return _SegmentResult(name=seg.name, ok=False, skipped=skipped, failed=len(pending), error=f"gm spawn error: {e}")

            if completed.returncode != 0:
                return _SegmentResult(
                    name=seg.name,
                    ok=False,
                    skipped=skipped,
                    failed=len(pending),
                    error=(completed.stderr or completed.stdout or "").strip()[:400],
                )

        # Verify each expected thumb exists, count generated vs failed.
        generated = 0
        failed = 0
        for img in pending:
            thumb = dst_dir / img.name
            if thumb.exists() and thumb.stat().st_size > 0:
                generated += 1
            else:
                failed += 1
        return _SegmentResult(
            name=seg.name,
            ok=(failed == 0),
            generated=generated,
            skipped=skipped,
            failed=failed,
        )

    # ---------- manifest + progress ----------

    def _write_manifest(self) -> None:
        data = {
            "year": self.plan.year,
            "dest_folder": str(self.plan.dest_folder),
            "segments_total": self._segments_total,
            "segments_done": self._segments_done,
            "images_done": self._files_done,
            "images_skipped": self._files_skipped,
            "images_failed": self._files_failed,
            "failures": self._failures[:500],
        }
        try:
            manifest.write_manifest(self.plan.dest_folder / MANIFEST_NAME, data)
        except Exception as e:  # noqa: BLE001
            self._emit("error", f"Manifest write failed: {e}")
            self.cancel.set()

    def _emit(self, phase: str, message: str = "") -> None:
        self.q.put(self._make_event(phase, message))

    def _emit_progress(self, message: str) -> None:
        ev = self._make_event("copying", message)
        try:
            self.q.put(ev, timeout=0.5)
        except queue.Full:
            pass

    def _make_event(self, phase: str, message: str) -> ProgressEvent:
        ev = ProgressEvent(
            phase=phase,  # type: ignore[arg-type]
            message=message,
            files_total=self._files_total,
            files_done=self._files_done,
            files_skipped=self._files_skipped,
            files_failed=self._files_failed,
        )
        now = time.monotonic()
        self._samples.append((now, self._files_done))
        cutoff = now - THROUGHPUT_WINDOW_S
        while len(self._samples) > 2 and self._samples[0][0] < cutoff:
            self._samples.pop(0)
        if len(self._samples) >= 2:
            t0, f0 = self._samples[0]
            dt = max(now - t0, 1e-6)
            ev.files_per_sec = (self._files_done - f0) / dt
            remaining = max(self._files_total - self._files_done, 0)
            if ev.files_per_sec > 0:
                ev.eta_seconds = remaining / ev.files_per_sec
        return ev


def _list_images(folder: Path) -> list[Path]:
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return []
    out: list[Path] = []
    for entry in entries:
        if entry.is_file() and os.path.splitext(entry.name)[1].lower() in IMAGE_EXTS:
            out.append(Path(entry.path))
    out.sort()
    return out


def _thumb_is_fresh(thumb: Path, source: Path) -> bool:
    try:
        t = thumb.stat()
        s = source.stat()
    except OSError:
        return False
    return t.st_size > 0 and t.st_mtime >= s.st_mtime


# Windows CreateProcess caps the command line at 32,767 chars including the exe
# path and quoting overhead. Stay well under that to leave room for cmd_base.
_MAX_CMDLINE = 28000


def _batch_filenames(filenames: list[str], cmd_base: list[str]) -> list[list[str]]:
    """Split filenames into chunks whose total argv length stays under the OS limit."""
    if not filenames:
        return []
    base_len = sum(len(a) + 3 for a in cmd_base)  # +3 ≈ quotes + separator
    batches: list[list[str]] = []
    current: list[str] = []
    current_len = base_len
    for name in filenames:
        cost = len(name) + 3
        if current and current_len + cost > _MAX_CMDLINE:
            batches.append(current)
            current = []
            current_len = base_len
        current.append(name)
        current_len += cost
    if current:
        batches.append(current)
    return batches
