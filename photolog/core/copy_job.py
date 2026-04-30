"""Resumable copy job: source tree -> N:\\RPMS$\\<year>\\.

Design:
- Manifest JSON at <dest_root>\\.photolog-copy-manifest.json lists every file
  with size/mtime and a state: pending | done | partial.
- Files are copied via a .partial sidecar then os.replace'd. Interrupted
  writes leave the .partial behind so resumption is observable on disk.
- On resume: 'done' entries whose dest size+mtime still match are skipped.
  Anything else gets SHA-256 compared; identical bytes are adopted,
  otherwise the dest is deleted and recopied.
- Manifest is rewritten periodically (not per-file) to avoid thrashing
  small-file bursts. A shutdown flush is always attempted.
"""
from __future__ import annotations

import os
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from photolog.core import dev, fs, manifest
from photolog.core.events import ProgressEvent

MANIFEST_NAME = ".photolog-copy-manifest.json"
PARTIAL_SUFFIX = ".photolog-partial"
MANIFEST_FLUSH_FILES = 5
MANIFEST_FLUSH_SECONDS = 5.0
THROUGHPUT_WINDOW_S = 30.0


@dataclass
class FileEntry:
    rel: str
    size: int
    mtime: float
    state: str = "pending"  # pending | done | partial
    sha256: str | None = None

    def to_dict(self) -> dict:
        d = {"rel": self.rel, "size": self.size, "mtime": self.mtime, "state": self.state}
        if self.sha256:
            d["sha256"] = self.sha256
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "FileEntry":
        return cls(
            rel=d["rel"],
            size=int(d["size"]),
            mtime=float(d["mtime"]),
            state=d.get("state", "pending"),
            sha256=d.get("sha256"),
        )


@dataclass
class CopyPlan:
    source_root: Path
    dest_root: Path
    year: int


class CopyJob:
    def __init__(
        self,
        plan: CopyPlan,
        out_queue: "queue.Queue[ProgressEvent]",
        cancel: threading.Event,
        pause: threading.Event,
    ):
        self.plan = plan
        self.q = out_queue
        self.cancel = cancel
        self.pause = pause  # set = paused
        self.manifest_path = plan.dest_root / MANIFEST_NAME
        self._files: list[FileEntry] = []
        self._last_flush_t = 0.0
        self._flush_counter = 0
        self._bytes_done = 0
        self._bytes_total = 0
        self._files_done = 0
        self._files_skipped = 0
        self._files_failed = 0
        self._samples: list[tuple[float, int, int]] = []  # (t, bytes_done, files_done)

    # ---------- public ----------

    def run(self) -> None:
        try:
            # Allow callers (e.g. the Full ingest tab) to pre-scan so they can
            # quote file/byte totals in a "started" notification before the
            # engine begins doing real I/O. Scanning is idempotent but walks
            # the whole source tree, so we don't want to do it twice.
            if not self._files:
                self._emit("scanning", "Scanning source…")
                self._scan_or_resume()
            self._bytes_total = sum(f.size for f in self._files)
            self._bytes_done = sum(f.size for f in self._files if f.state == "done")
            self._files_done = sum(1 for f in self._files if f.state == "done")
            # Check destination has enough free space before starting copy
            bytes_remaining = self._bytes_total - self._bytes_done
            if not self._check_disk_space(bytes_remaining):
                return
            self._emit("copying", f"Copying {len(self._files)} files…")
            self._copy_all()
            if self.cancel.is_set():
                self._flush_manifest(force=True)
                self._emit("cancelled", "Cancelled.")
                return
            self._emit("verifying", "Verifying…")
            self._verify()
            self._flush_manifest(force=True)
            self._emit("done", "Copy complete.")
        except Exception as e:  # noqa: BLE001
            self._flush_manifest(force=True)
            self._emit("error", f"Copy failed: {e}")
        finally:
            self._flush_manifest(force=True)

    # ---------- scan / resume ----------

    def _scan_or_resume(self) -> None:
        self.plan.dest_root.mkdir(parents=True, exist_ok=True)
        prev = manifest.read_manifest(self.manifest_path)
        known: dict[str, FileEntry] = {}
        if prev is not None:
            prev_source = prev.get("source_root", "")
            if prev_source and prev_source != str(self.plan.source_root):
                raise RuntimeError(
                    f"Destination {self.plan.dest_root} has a manifest from a different source "
                    f"({prev_source!r}). Pick a different destination or resume with the original source. "
                    f"Refusing to mix sources in one year folder."
                )
            for raw in prev.get("files", []):
                try:
                    entry = FileEntry.from_dict(raw)
                    known[entry.rel] = entry
                except (KeyError, ValueError):
                    continue

        scanned: list[FileEntry] = []
        for src in _walk_files(self.plan.source_root):
            if self.cancel.is_set():
                return
            rel = str(src.relative_to(self.plan.source_root)).replace("\\", "/")
            st = src.stat()
            prev_entry = known.get(rel)
            if prev_entry and prev_entry.size == st.st_size and abs(prev_entry.mtime - st.st_mtime) <= fs.MTIME_TOL_S:
                # reuse prior state
                entry = prev_entry
            else:
                entry = FileEntry(rel=rel, size=st.st_size, mtime=st.st_mtime, state="pending")
            scanned.append(entry)
        self._files = scanned
        self._flush_manifest(force=True)

    # ---------- copy ----------

    def _copy_all(self) -> None:
        self._samples.append((time.monotonic(), self._bytes_done, self._files_done))
        for i, entry in enumerate(self._files):
            if self.cancel.is_set():
                return
            self._await_unpause()
            # Check disk space every 10 files (expensive on large runs, so sample)
            if i % 10 == 0:
                bytes_remaining = sum(f.size for f in self._files[i:] if f.state != "done")
                if not self._check_disk_space(bytes_remaining, critical=True):
                    return
            try:
                self._process_entry(entry)
            except Exception as e:  # noqa: BLE001
                self._files_failed += 1
                self._emit("copying", f"Error on {entry.rel}: {e}", warning=f"{entry.rel}: {e}")
                entry.state = "partial"
            self._maybe_flush()

    def _process_entry(self, entry: FileEntry) -> None:
        # Dev hooks fire BEFORE any filesystem work so a crash-simulate leaves
        # the file untouched (exactly the interruption we want to resume from).
        if dev.SETTINGS.crash_at_copy_index == self._files_done:
            self._flush_manifest(force=True)
            self._emit("error", f"dev: simulated crash at file index {self._files_done}")
            os._exit(1)
        if dev.SETTINGS.fail_at_copy_index == self._files_done:
            raise RuntimeError(f"dev: simulated failure at file index {self._files_done}")

        src = self.plan.source_root / entry.rel
        dst = self.plan.dest_root / entry.rel
        partial = dst.with_name(dst.name + PARTIAL_SUFFIX)

        # Already done and still matches on disk -> skip
        if entry.state == "done" and dst.exists() and fs.files_look_equal(src, dst):
            self._files_skipped += 1
            return

        # Destination mismatch or partial -> hash-compare to avoid wasteful recopy
        if dst.exists() and not fs.files_look_equal(src, dst):
            if self._hashes_match(src, dst):
                entry.state = "done"
                entry.sha256 = None
                self._bytes_done += entry.size
                self._files_done += 1
                self._emit_progress(entry.rel, f"Adopted existing {entry.rel}")
                return
            try:
                dst.unlink()
            except OSError:
                pass

        if partial.exists():
            try:
                partial.unlink()
            except OSError as e:
                # Attempt to remove orphaned partial file; if it persists, warn but continue
                if partial.exists():
                    self._emit(
                        "copying",
                        f"Warning: failed to clean up partial file {partial.name}: {e}",
                        warning=f"{entry.rel}: partial cleanup failed",
                    )

        dst.parent.mkdir(parents=True, exist_ok=True)
        entry.state = "partial"
        self._copy_bytes(src, partial, entry)
        if self.cancel.is_set():
            return
        os.replace(partial, dst)
        try:
            os.utime(dst, (entry.mtime, entry.mtime))
        except OSError:
            pass
        entry.state = "done"
        self._files_done += 1
        self._emit_progress(entry.rel, f"Copied {entry.rel}")

    def _copy_bytes(self, src: Path, partial: Path, entry: FileEntry) -> None:
        with src.open("rb") as fsrc, partial.open("wb") as fdst:
            while True:
                if self.cancel.is_set():
                    return
                self._await_unpause()
                # Dev throttle: placed AFTER pause so Pause pre-empts the sleep.
                delay = dev.SETTINGS.copy_chunk_delay_s
                if delay > 0:
                    time.sleep(delay)
                chunk = fsrc.read(fs.COPY_CHUNK)
                if not chunk:
                    break
                fdst.write(chunk)
                self._bytes_done += len(chunk)
                if dev.SETTINGS.log_every_chunk:
                    self._emit_progress(entry.rel, f"chunk {len(chunk)} B {entry.rel}")
                else:
                    self._emit_progress(entry.rel)
            fdst.flush()
            try:
                os.fsync(fdst.fileno())
            except OSError:
                pass

    def _hashes_match(self, src: Path, dst: Path) -> bool:
        try:
            if src.stat().st_size != dst.stat().st_size:
                return False
        except OSError:
            return False
        try:
            return fs.sha256_file(src, self.cancel) == fs.sha256_file(dst, self.cancel)
        except InterruptedError:
            return False

    # ---------- disk space checks ----------

    def _check_disk_space(self, bytes_remaining: int, critical: bool = False) -> bool:
        """Check if destination has enough free space. Returns True if OK, False if aborting."""
        try:
            u = __import__("shutil").disk_usage(str(self.plan.dest_root))
            free = u.free
        except OSError:
            # Can't check free space; proceed optimistically
            return True
        # Require 1.5× the remaining bytes as safety margin
        required = int(bytes_remaining * 1.5)
        if free < required:
            msg = (
                f"Insufficient disk space: {fs.human_bytes(free)} free, but "
                f"{fs.human_bytes(bytes_remaining)} still to copy. Aborting."
            )
            self._emit("error", msg)
            self.cancel.set()
            self._flush_manifest(force=True)
            return False
        return True

    # ---------- verify ----------

    def _verify(self) -> None:
        for entry in self._files:
            if self.cancel.is_set():
                return
            dst = self.plan.dest_root / entry.rel
            if not dst.exists() or dst.stat().st_size != entry.size:
                entry.state = "partial"
                self._files_failed += 1

    # ---------- manifest ----------

    def _maybe_flush(self) -> None:
        self._flush_counter += 1
        now = time.monotonic()
        if self._flush_counter >= MANIFEST_FLUSH_FILES or (now - self._last_flush_t) >= MANIFEST_FLUSH_SECONDS:
            self._flush_manifest(force=True)

    def _flush_manifest(self, force: bool = False) -> None:
        if not force and self._flush_counter == 0:
            return
        data = {
            "source_root": str(self.plan.source_root),
            "dest_root": str(self.plan.dest_root),
            "year": self.plan.year,
            "files": [e.to_dict() for e in self._files],
        }
        try:
            manifest.write_manifest(self.manifest_path, data)
        except Exception as e:  # noqa: BLE001
            # Manifest write failure is critical; report and halt the job
            self._emit("error", f"Manifest write failed (job halted): {e}")
            self.cancel.set()
        self._flush_counter = 0
        self._last_flush_t = time.monotonic()

    # ---------- pause / progress ----------

    def _await_unpause(self) -> None:
        if self.pause.is_set():
            self._flush_manifest(force=True)  # persist progress before sleeping
        while self.pause.is_set() and not self.cancel.is_set():
            time.sleep(0.1)

    def _emit(self, phase: str, message: str = "", *, warning: str | None = None) -> None:
        ev = ProgressEvent(
            phase=phase,  # type: ignore[arg-type]
            message=message,
            files_total=len(self._files),
            files_done=self._files_done,
            files_skipped=self._files_skipped,
            files_failed=self._files_failed,
            bytes_total=self._bytes_total,
            bytes_done=self._bytes_done,
        )
        if warning:
            ev.warnings.append(warning)
        self._fill_throughput(ev)
        self.q.put(ev)

    def _emit_progress(self, current_file: str, message: str = "") -> None:
        ev = ProgressEvent(
            phase="copying",
            message=message,
            files_total=len(self._files),
            files_done=self._files_done,
            files_skipped=self._files_skipped,
            files_failed=self._files_failed,
            bytes_total=self._bytes_total,
            bytes_done=self._bytes_done,
            current_file=current_file,
        )
        self._fill_throughput(ev)
        # Drop older progress events if the UI hasn't caught up, but never
        # drop the final ones (they'd carry terminal state).
        try:
            self.q.put(ev, timeout=0.5)
        except queue.Full:
            pass

    def _fill_throughput(self, ev: ProgressEvent) -> None:
        now = time.monotonic()
        self._samples.append((now, self._bytes_done, self._files_done))
        cutoff = now - THROUGHPUT_WINDOW_S
        while len(self._samples) > 2 and self._samples[0][0] < cutoff:
            self._samples.pop(0)
        if len(self._samples) >= 2:
            t0, b0, f0 = self._samples[0]
            dt = max(now - t0, 1e-6)
            ev.bytes_per_sec = (self._bytes_done - b0) / dt
            ev.files_per_sec = (self._files_done - f0) / dt
            remaining = max(self._bytes_total - self._bytes_done, 0)
            if ev.bytes_per_sec > 0:
                ev.eta_seconds = remaining / ev.bytes_per_sec


def _walk_files(root: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            if name.startswith(".photolog-") or name.endswith(PARTIAL_SUFFIX):
                continue
            yield Path(dirpath) / name
