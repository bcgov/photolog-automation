"""Tab 2: Generate thumbnails via GraphicsMagick.

Flow:
    1. Pick a source folder and a year.
    2. `thumb_detect.interpret_thumb_source` classifies segments.
    3. PreflightCard shows count/size; user hits Generate.
    4. Per-segment `gm mogrify -output-directory …` runs in N workers.
"""
from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from photolog.core import junctions, notify, paths
from photolog.core.events import ProgressEvent
from photolog.core.fs import human_bytes, human_duration
from photolog.core.thumb_detect import Refusal, ThumbInterpretation, interpret_thumb_source
from photolog.core.thumb_job import DEFAULT_WORKERS, ThumbJob, ThumbPlan
from photolog.ui.preflight import PreflightCard
from photolog.ui.widgets import LogPane, ProgressBlock

JOB_KIND = "Thumbnail generation"


class ThumbTab(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master)
        self._q: queue.Queue[ProgressEvent] = queue.Queue(maxsize=200)
        self._cancel = threading.Event()
        self._worker: threading.Thread | None = None
        self._interpretation: ThumbInterpretation | None = None
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
            text="Pick the '<year> Highway Images' folder. "
                 "Thumbnails will be written to <TN root>\\<year>\\<segment>\\ (e.g. N:\\RPM\\TN\\2025\\H1_E_00_km0000\\).",
            text_color=("gray50", "gray60"),
            font=ctk.CTkFont(size=11),
            wraplength=600, justify="left",
        ).grid(row=1, column=0, columnspan=3, sticky="w", padx=12, pady=(0, 4))

        ctk.CTkLabel(self, text="Year:").grid(row=2, column=0, sticky="w", padx=8, pady=6)
        self.year_var = tk.StringVar()
        self.year_combo = ctk.CTkComboBox(
            self, values=self._year_options(),
            variable=self.year_var, width=140,
        )
        self.year_combo.grid(row=2, column=1, sticky="w", padx=4)
        ctk.CTkButton(self, text="Refresh", width=80, command=self._refresh_years).grid(row=2, column=2, padx=8)
        self.year_var.trace_add("write", lambda *_: self._rerender_preflight())

        opts = ctk.CTkFrame(self, fg_color="transparent")
        opts.grid(row=3, column=0, columnspan=3, sticky="ew", padx=8, pady=4)
        self.skip_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(opts, text="Skip existing thumbnails", variable=self.skip_var).pack(side="left", padx=4)
        ctk.CTkLabel(opts, text="Workers:").pack(side="left", padx=(16, 4))
        self.workers_var = tk.StringVar(value=str(DEFAULT_WORKERS))
        ctk.CTkEntry(opts, textvariable=self.workers_var, width=60).pack(side="left")

        self.preflight = PreflightCard(
            self, on_start=self._start, on_pick_different=self._pick_source
        )
        self.preflight.grid(row=4, column=0, columnspan=3, sticky="ew", padx=8, pady=(2, 8))

        self.progress = ProgressBlock(self)
        self.progress.grid(row=5, column=0, columnspan=3, sticky="ew", padx=8, pady=4)

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.grid(row=6, column=0, columnspan=3, sticky="ew", padx=8)
        self.cancel_btn = ctk.CTkButton(btn_row, text="Cancel", command=self._cancel_job, state="disabled")
        self.cancel_btn.pack(side="left")

        ctk.CTkLabel(self, text="Log").grid(row=7, column=0, sticky="w", padx=8, pady=(8, 0))
        self.log = LogPane(self)
        self.log.grid(row=8, column=0, columnspan=3, sticky="nsew", padx=8, pady=(0, 8))
        self.grid_rowconfigure(8, weight=1)

    # ---------- external hooks ----------

    def refresh_from_config(self) -> None:
        self.year_combo.configure(values=self._year_options())
        if self._interpretation is not None:
            self._rerender_preflight()

    def _year_options(self) -> list[str]:
        return [str(y) for y in paths.detect_years_on_drive()] or [""]

    def _refresh_years(self) -> None:
        self.year_combo.configure(values=self._year_options())

    # ---------- source picker + detector ----------

    def _pick_source(self) -> None:
        try:
            initial = str(paths.hr_root())
        except RuntimeError:
            initial = ""
        d = filedialog.askdirectory(
            title="Select Highway Images folder (or a single segment)",
            initialdir=initial or None,
        )
        if not d:
            return
        self.source_var.set(d)
        self._interpret(Path(d))

    def _interpret(self, path: Path) -> None:
        if not paths.is_configured():
            self.preflight.show_refusal(
                "Destination and gm.exe aren't configured yet. Open Settings first."
            )
            self._interpretation = None
            return
        result = interpret_thumb_source(path)
        if isinstance(result, Refusal):
            self._interpretation = None
            self.preflight.show_refusal(result.reason)
            self._log(f"Refused: {result.reason}")
            return
        self._interpretation = result
        self._rerender_preflight()

    def _rerender_preflight(self) -> None:
        interp = self._interpretation
        if interp is None:
            return

        year_str = self.year_var.get().strip()
        year = int(year_str) if year_str.isdigit() and 1990 <= int(year_str) <= 2100 else None
        try:
            dest_root = str(paths.year_tn_root(year)) if year else "(pick year)"
        except RuntimeError:
            dest_root = "(TN root not configured)"

        shape_label = {
            "highway_images": "Highway Images folder — subfolders are segments",
            "year_folder":    "Year folder — auto-descended into Highway Images",
            "single_segment": "Single segment (images directly under picked folder)",
            "loose_images":   "Loose images (treated as one segment)",
        }.get(interp.shape, interp.shape)

        output_pattern = f"{dest_root}\\<segment>\\" if year else "(pick year)"
        try:
            tn_link = str(paths.tn_link_root() / str(year)) if year else "(pick year)"
        except RuntimeError:
            tn_link = "(TN link root not configured)"

        rows = [
            ("Generating thumbnails FROM", str(interp.source_root)),
            ("Folder layout", shape_label),
            ("Year", str(year) if year else "(pick year above)"),
            ("Segments found", str(len(interp.segments))),
            ("Images total", f"{interp.total_images:,}   ({human_bytes(interp.total_bytes)})"),
            ("Will write thumbnails TO", output_pattern),
            ("Junction will be created at", tn_link),
        ]

        warnings: list[str] = []
        if year is None:
            warnings.append("Pick a year above — year is never inferred from folder name.")

        self.preflight.show_interpretation(
            title=f"Thumbnail preflight — {interp.shape.replace('_', ' ')}",
            rows=rows,
            notes=interp.notes,
            warnings=warnings,
            start_label="Generate thumbnails",
            start_enabled=(year is not None and len(interp.segments) > 0),
        )

    # ---------- job lifecycle ----------

    def _start(self) -> None:
        if self._interpretation is None:
            messagebox.showerror("Photolog", "Pick a source and confirm preflight first.")
            return
        year_str = self.year_var.get().strip()
        if not year_str.isdigit() or not (1990 <= int(year_str) <= 2100):
            messagebox.showerror("Photolog", "Pick a year.")
            return
        year = int(year_str)
        try:
            workers = max(1, int(self.workers_var.get()))
        except ValueError:
            workers = DEFAULT_WORKERS
        try:
            dest_folder = paths.year_tn_root(year)
            gm_exe = paths.gm_executable()
        except RuntimeError as e:
            messagebox.showerror("Photolog", str(e))
            return
        if not gm_exe.exists():
            messagebox.showerror("Photolog", f"gm executable not found at {gm_exe}. Open Settings.")
            return

        try:
            plan = ThumbPlan(
                year=year,
                segments=self._interpretation.segments,
                dest_folder=dest_folder,
                gm_exe=gm_exe,
                skip_existing=self.skip_var.get(),
                workers=workers,
            )
        except ValueError as e:
            messagebox.showerror("Photolog — source/dest overlap", str(e))
            return

        self._cancel.clear()
        self._log(f"Starting thumbnails: {len(plan.segments)} segment(s) -> {dest_folder}")
        self._set_running(True)
        self._year = year
        self._dest = dest_folder
        self._worker = threading.Thread(target=self._run, args=(plan,), daemon=False)
        self._worker.start()

    def _run(self, plan: ThumbPlan) -> None:
        started_at = time.monotonic()
        report = notify.JobReport(outcome="ok")
        job = ThumbJob(plan, self._q, self._cancel)
        # Resolve before the try so the finally's notify.send_finished() never
        # NameErrors if an early exception fires before this would be assigned.
        source_label = str(plan.segments[0].source_folder.parent) if plan.segments else ""
        try:
            files_total = sum(s.image_count for s in plan.segments)
            bytes_total = sum(s.bytes_total for s in plan.segments)
            notify.send_started(
                job_kind=JOB_KIND, year=self._year,
                source=source_label,
                dest=str(plan.dest_folder),
                files_total=files_total, bytes_total=bytes_total,
                segments=len(plan.segments),
            )
            job.run()
            if self._cancel.is_set():
                report.outcome = "cancelled"
            else:
                try:
                    link = paths.tn_link_root() / str(self._year)
                    result = junctions.ensure_junction(link, self._dest)
                    self._q.put(ProgressEvent(
                        phase="linking",
                        message=f"TN junction {result}: {link} -> {self._dest}",
                    ))
                except junctions.JunctionConflict as e:
                    report.outcome = "error"
                    report.error = f"Junction conflict: {e}"
                    self._q.put(ProgressEvent(phase="error", message=report.error))
                except Exception as e:  # noqa: BLE001
                    report.outcome = "error"
                    report.error = f"Junction error: {e}"
                    self._q.put(ProgressEvent(phase="error", message=report.error))
            self._q.put(ProgressEvent(phase="done", message="(job thread exiting)"))
        finally:
            report.files_total = job._files_total  # noqa: SLF001
            report.files_done = job._files_done  # noqa: SLF001
            report.files_skipped = job._files_skipped  # noqa: SLF001
            report.files_failed = job._files_failed  # noqa: SLF001
            report.duration_s = time.monotonic() - started_at
            notify.send_finished(
                job_kind=JOB_KIND, year=self._year,
                source=source_label,
                dest=str(plan.dest_folder),
                report=report, segments=len(plan.segments),
            )

    def _cancel_job(self) -> None:
        if messagebox.askyesno("Cancel", "Cancel thumbnail generation?"):
            self._cancel.set()
            self._log("Cancelling…")

    def _set_running(self, running: bool) -> None:
        self.preflight.set_start_enabled(not running)
        self.cancel_btn.configure(state="normal" if running else "disabled")

    # ---------- pump ----------

    def _pump(self) -> None:
        terminal = False
        try:
            while True:
                ev = self._q.get_nowait()
                self._apply(ev)
                if ev.phase in ("done", "error", "cancelled"):
                    terminal = True
        except queue.Empty:
            pass
        if terminal:
            self._set_running(False)
        self.after(200, self._pump)

    def _apply(self, ev: ProgressEvent) -> None:
        if ev.files_total > 0:
            self.progress.set_fraction(ev.files_done / ev.files_total)
        self.progress.set_status(
            f"[{ev.phase}] {ev.files_done}/{ev.files_total} thumbs"
            + (f" — {ev.current_file}" if ev.current_file else "")
        )
        self.progress.set_metrics(
            f"{ev.files_per_sec:.1f} files/s   •   ETA {human_duration(ev.eta_seconds)}"
            f"   •   skipped {ev.files_skipped}   failed {ev.files_failed}"
        )
        if ev.message:
            self._log(ev.message)

    def _log(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.log.append(f"{ts}  {msg}")
