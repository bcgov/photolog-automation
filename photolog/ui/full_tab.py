"""Tab: Full ingest — copy USB + generate thumbnails + create both junctions.

One-click chain of the existing Copy and Thumbnail jobs. The two engines are
reused unchanged; this tab is a thin orchestrator that runs them back-to-back
on a single worker thread:

    CopyJob -> HR junction -> interpret thumb source -> ThumbJob -> TN junction

Intermediate "done" events from CopyJob and ThumbJob are treated as phase
transitions, not as terminal signals. A local sentinel phase (`_INGEST_DONE`)
marks the true end of the combined run.
"""
from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from photolog.core import junctions, paths
from photolog.core.copy_detect import CopyInterpretation
from photolog.core.copy_detect import Refusal as CopyRefusal
from photolog.core.copy_detect import interpret_copy_source
from photolog.core.copy_job import MANIFEST_NAME, CopyJob, CopyPlan
from photolog.core.events import ProgressEvent
from photolog.core.fs import human_bytes, human_duration
from photolog.core.thumb_detect import Refusal as ThumbRefusal
from photolog.core.thumb_detect import interpret_thumb_source
from photolog.core.thumb_job import DEFAULT_WORKERS, ThumbJob, ThumbPlan
from photolog.ui.preflight import PreflightCard
from photolog.ui.widgets import LogPane, ProgressBlock

# Sub-jobs emit phase="done" when they finish their part. In this tab those
# are phase transitions, not terminal. This sentinel — enqueued only by the
# wrapper after every step has succeeded — is what flips the UI back to idle.
_INGEST_DONE = "ingest_done"


class FullTab(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master)
        self._q: queue.Queue[ProgressEvent] = queue.Queue(maxsize=200)
        self._cancel = threading.Event()
        self._pause = threading.Event()
        self._worker: threading.Thread | None = None
        self._interpretation: CopyInterpretation | None = None
        self._build()
        self.after(200, self._pump)

    # ---------- build ----------

    def _build(self) -> None:
        self.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(self, text="Source folder:").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        self.source_var = tk.StringVar()
        ctk.CTkEntry(self, textvariable=self.source_var).grid(row=0, column=1, sticky="ew", padx=4)
        ctk.CTkButton(self, text="Browse…", width=80, command=self._pick_source).grid(row=0, column=2, padx=8)

        ctk.CTkLabel(
            self,
            text="Pick the USB root or the year folder on the USB (e.g. E:\\  or  E:\\2025\\). "
                 "Files will be copied to <HR root>\\<year>\\, then thumbnails generated into "
                 "<TN root>\\<year>\\, and both junctions created.",
            text_color=("gray50", "gray60"),
            font=ctk.CTkFont(size=11),
            wraplength=600, justify="left",
        ).grid(row=1, column=0, columnspan=3, sticky="w", padx=12, pady=(0, 4))

        self.preflight = PreflightCard(
            self, on_start=self._start, on_pick_different=self._pick_source
        )
        self.preflight.grid(row=2, column=0, columnspan=3, sticky="ew", padx=8, pady=(2, 8))

        self.progress = ProgressBlock(self)
        self.progress.grid(row=3, column=0, columnspan=3, sticky="ew", padx=8, pady=4)

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.grid(row=4, column=0, columnspan=3, sticky="ew", padx=8)
        self.pause_btn = ctk.CTkButton(btn_row, text="Pause", command=self._toggle_pause, state="disabled")
        self.pause_btn.pack(side="left")
        self.cancel_btn = ctk.CTkButton(btn_row, text="Cancel", command=self._cancel_job, state="disabled")
        self.cancel_btn.pack(side="left", padx=8)

        ctk.CTkLabel(self, text="Log").grid(row=5, column=0, sticky="w", padx=8, pady=(8, 0))
        self.log = LogPane(self)
        self.log.grid(row=6, column=0, columnspan=3, sticky="nsew", padx=8, pady=(0, 8))
        self.grid_rowconfigure(6, weight=1)

    # ---------- external hooks ----------

    def refresh_from_config(self) -> None:
        if self._interpretation is not None:
            self._render_preflight(self._interpretation)

    # ---------- source picker + detector ----------

    def _pick_source(self) -> None:
        d = filedialog.askdirectory(title="Select source folder (USB, year dump, etc.)")
        if not d:
            return
        self.source_var.set(d)
        self._interpret(Path(d))

    def _interpret(self, path: Path) -> None:
        if not paths.is_configured():
            self.preflight.show_refusal(
                "Destination paths aren't configured yet. Open Settings first."
            )
            self._interpretation = None
            return
        result = interpret_copy_source(path)
        if isinstance(result, CopyRefusal):
            self._interpretation = None
            self.preflight.show_refusal(result.reason)
            self._log(f"Refused: {result.reason}")
            return
        self._interpretation = result
        self._render_preflight(result)

    def _render_preflight(self, interp: CopyInterpretation) -> None:
        year = interp.year
        dest = self._safe_path(lambda: str(paths.year_dest_root(year)), year, "HR root")
        tn_dest = self._safe_path(lambda: str(paths.year_tn_root(year)), year, "TN root")
        hr_link = self._safe_path(lambda: str(paths.hr_link_root() / str(year)), year, "HR link root")
        tn_link = self._safe_path(lambda: str(paths.tn_link_root() / str(year)), year, "TN link root")

        shape_label = {
            "year_container": "Year container (descended into year subfolder)",
            "year_contents":  "Year contents (picked folder is the year payload)",
            "bare_dump":      "Bare dump — year must be entered manually",
        }.get(interp.shape, interp.shape)

        rows: list[tuple[str, str]] = [
            ("Copying FROM", str(interp.source_root)),
            ("Folder layout", shape_label),
            ("Year", str(year) if year is not None else "(enter below)"),
            ("1) Copy files TO", dest),
            ("2) Generate thumbnails TO", tn_dest),
            ("3) HR junction at", hr_link),
            ("4) TN junction at", tn_link),
            ("Highway Images segments", str(interp.segment_count) if interp.segment_count else "— (no Highway Images folder seen)"),
            ("Resume / fresh run", self._manifest_state(interp, year)),
        ]
        warnings: list[str] = []
        if interp.shape == "bare_dump":
            warnings.append("Couldn't detect a year from the folder. Enter it in the field below before starting.")
        if not interp.segment_count:
            warnings.append("No Highway Images folder seen — thumbnail phase will be skipped automatically.")

        self.preflight.show_interpretation(
            title=f"Full ingest preflight — {interp.shape.replace('_', ' ')}",
            rows=rows,
            notes=interp.notes,
            warnings=warnings,
            start_label="Start full ingest",
            start_enabled=(year is not None),
        )
        self._ensure_year_entry(needed=interp.shape == "bare_dump", seed=year)

    @staticmethod
    def _safe_path(getter, year: int | None, what: str) -> str:
        if year is None:
            return "(pick year)"
        try:
            return getter()
        except RuntimeError:
            return f"({what} not configured)"

    def _ensure_year_entry(self, *, needed: bool, seed: int | None) -> None:
        if needed and not hasattr(self, "_year_var"):
            self._year_var = tk.StringVar(value=str(seed) if seed else "")
            self._year_frame = ctk.CTkFrame(self, fg_color="transparent")
            self._year_frame.grid(row=7, column=0, columnspan=3, sticky="ew", padx=8)
            ctk.CTkLabel(self._year_frame, text="Year:").pack(side="left", padx=4)
            self._year_entry = ctk.CTkEntry(self._year_frame, textvariable=self._year_var, width=100)
            self._year_entry.pack(side="left", padx=4)
            self._year_var.trace_add("write", lambda *_: self._on_year_entered())
        elif not needed and hasattr(self, "_year_frame"):
            self._year_frame.destroy()
            del self._year_frame, self._year_var, self._year_entry

    def _on_year_entered(self) -> None:
        if self._interpretation is None or self._interpretation.shape != "bare_dump":
            return
        raw = self._year_var.get().strip()
        if raw.isdigit() and 1990 <= int(raw) <= 2100:
            self._interpretation.year = int(raw)
            self._render_preflight(self._interpretation)

    def _manifest_state(self, interp: CopyInterpretation, year: int | None) -> str:
        if year is None:
            return "— (year not set)"
        try:
            dest = paths.year_dest_root(year)
        except RuntimeError:
            return "— (HR root not configured)"
        mf = dest / MANIFEST_NAME
        if not mf.exists():
            return "fresh run (no manifest)"
        try:
            data = json.loads(mf.read_text(encoding="utf-8"))
            if data.get("source_root") == str(interp.source_root):
                return "resume ✓ (matching source)"
            return f"conflict — manifest source is {data.get('source_root')!r}"
        except (OSError, ValueError):
            return "present but unreadable"

    # ---------- job lifecycle ----------

    def _start(self) -> None:
        if self._interpretation is None or self._interpretation.year is None:
            messagebox.showerror("Photolog", "Finish the preflight first.")
            return
        interp = self._interpretation
        try:
            dest = paths.year_dest_root(interp.year)
        except RuntimeError as e:
            messagebox.showerror("Photolog", str(e))
            return
        if not dest.exists():
            if not messagebox.askyesno("Create destination?", f"{dest}\n\nDoes not exist. Create it?"):
                return
            dest.mkdir(parents=True, exist_ok=True)
        self._cancel.clear()
        self._pause.clear()
        plan = CopyPlan(source_root=interp.source_root, dest_root=dest, year=interp.year)
        self._log(f"Starting full ingest: {interp.source_root} -> {dest}")
        self._set_running(True)
        self._worker = threading.Thread(target=self._run, args=(plan,), daemon=True)
        self._worker.start()

    def _run(self, plan: CopyPlan) -> None:
        try:
            # Phase 1 — copy
            CopyJob(plan, self._q, self._cancel, self._pause).run()
            if self._cancel.is_set():
                self._enqueue("cancelled", "Full ingest cancelled during copy.")
                return

            # Phase 2 — HR junction (unless no Highway Images found)
            hr_folder = paths.find_highway_images_folder(plan.dest_root, plan.year)
            if hr_folder is None:
                self._enqueue(
                    "linking",
                    f"No '{plan.year} Highway Images' folder found; skipping HR junction and thumbnails.",
                    warnings=[f"Missing {plan.year} Highway Images"],
                )
                self._enqueue(_INGEST_DONE, "Copy complete. Thumbnails skipped.")
                return
            try:
                link = paths.hr_link_root() / str(plan.year)
                result = junctions.ensure_junction(link, hr_folder)
                self._enqueue("linking", f"HR junction {result}: {link} -> {hr_folder}")
            except junctions.JunctionConflict as e:
                self._enqueue("error", f"HR junction conflict: {e}")
                return
            except Exception as e:  # noqa: BLE001
                self._enqueue("error", f"HR junction error: {e}")
                return

            # Phase 3 — interpret the just-copied year folder as a thumb source
            thumb_interp = interpret_thumb_source(paths.year_dest_root(plan.year))
            if isinstance(thumb_interp, ThumbRefusal):
                self._enqueue(
                    "linking",
                    f"Thumb preflight skipped: {thumb_interp.reason}",
                    warnings=[thumb_interp.reason],
                )
                self._enqueue(_INGEST_DONE, "Copy + HR junction complete. Thumbnails skipped.")
                return
            if not thumb_interp.segments:
                self._enqueue("linking", "No segments found in copied tree; thumbnails skipped.")
                self._enqueue(_INGEST_DONE, "Copy + HR junction complete. Thumbnails skipped.")
                return

            # Phase 4 — thumbnails
            try:
                tn_dest = paths.year_tn_root(plan.year)
                gm_exe = paths.gm_executable()
            except RuntimeError as e:
                self._enqueue("error", str(e))
                return
            try:
                thumb_plan = ThumbPlan(
                    year=plan.year,
                    segments=thumb_interp.segments,
                    dest_folder=tn_dest,
                    gm_exe=gm_exe,
                    skip_existing=True,
                    workers=DEFAULT_WORKERS,
                )
            except ValueError as e:
                self._enqueue("error", f"Thumb plan invalid: {e}")
                return

            self._enqueue(
                "copying",
                f"Starting thumbnails: {len(thumb_plan.segments)} segment(s) -> {tn_dest}",
            )
            ThumbJob(thumb_plan, self._q, self._cancel).run()
            if self._cancel.is_set():
                self._enqueue("cancelled", "Full ingest cancelled during thumbnails.")
                return

            # Phase 5 — TN junction
            try:
                tn_link = paths.tn_link_root() / str(plan.year)
                result = junctions.ensure_junction(tn_link, tn_dest)
                self._enqueue("linking", f"TN junction {result}: {tn_link} -> {tn_dest}")
            except junctions.JunctionConflict as e:
                self._enqueue("error", f"TN junction conflict: {e}")
                return
            except Exception as e:  # noqa: BLE001
                self._enqueue("error", f"TN junction error: {e}")
                return

            self._enqueue(_INGEST_DONE, "Full ingest complete.")
        except Exception as e:  # noqa: BLE001
            self._enqueue("error", f"Full ingest failed: {e}")

    def _enqueue(self, phase: str, message: str, *, warnings: list[str] | None = None) -> None:
        ev = ProgressEvent(phase=phase, message=message)  # type: ignore[arg-type]
        if warnings:
            ev.warnings.extend(warnings)
        self._q.put(ev)

    def _toggle_pause(self) -> None:
        # Pause only affects the copy phase — ThumbJob has no pause gate.
        if self._pause.is_set():
            self._pause.clear()
            self.pause_btn.configure(text="Pause")
            self._log("Resumed")
        else:
            self._pause.set()
            self.pause_btn.configure(text="Resume")
            self._log("Paused (copy phase only)")

    def _cancel_job(self) -> None:
        if messagebox.askyesno("Cancel", "Cancel the full ingest? You can resume later."):
            self._cancel.set()
            self._log("Cancelling…")

    def _set_running(self, running: bool) -> None:
        self.preflight.set_start_enabled(not running)
        self.pause_btn.configure(state="normal" if running else "disabled", text="Pause")
        self.cancel_btn.configure(state="normal" if running else "disabled")

    # ---------- queue pump ----------

    def _pump(self) -> None:
        terminal = False
        try:
            while True:
                ev = self._q.get_nowait()
                self._apply(ev)
                if ev.phase in (_INGEST_DONE, "error", "cancelled"):
                    terminal = True
        except queue.Empty:
            pass
        if terminal:
            self._set_running(False)
        self.after(200, self._pump)

    def _apply(self, ev: ProgressEvent) -> None:
        # Copy events carry byte counters; thumb events only carry file counters.
        if ev.bytes_total > 0:
            self.progress.set_fraction(ev.bytes_done / ev.bytes_total)
        elif ev.files_total > 0:
            self.progress.set_fraction(ev.files_done / ev.files_total)
        phase_label = "done" if ev.phase == _INGEST_DONE else ev.phase
        self.progress.set_status(
            f"[{phase_label}] {ev.files_done}/{ev.files_total} files"
            + (f" — {ev.current_file}" if ev.current_file else "")
        )
        if ev.bytes_total > 0:
            metrics = (
                f"{human_bytes(ev.bytes_done)} / {human_bytes(ev.bytes_total)}   •   "
                f"{human_bytes(ev.bytes_per_sec)}/s   •   "
            )
        else:
            metrics = f"{ev.files_per_sec:.1f} files/s   •   "
        metrics += (
            f"ETA {human_duration(ev.eta_seconds)}"
            f"   •   skipped {ev.files_skipped}   failed {ev.files_failed}"
        )
        self.progress.set_metrics(metrics)
        if ev.message:
            self._log(ev.message)
        for w in ev.warnings:
            self._log(f"WARN: {w}")

    def _log(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.log.append(f"{ts}  {msg}")
