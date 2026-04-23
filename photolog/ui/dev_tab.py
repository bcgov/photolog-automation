"""Dev-mode tab: throttles, fault injection, sim-tree seeding, manifest tools.

This module is excluded from the PyInstaller prod build via --exclude-module,
so it can freely import heavier helpers (dev_fixtures) without worrying about
bloating the shipped binary.
"""
from __future__ import annotations

import json
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from photolog.core import dev, dev_fixtures, paths
from photolog.core.copy_job import MANIFEST_NAME as COPY_MANIFEST, PARTIAL_SUFFIX
from photolog.core.thumb_job import MANIFEST_NAME as THUMB_MANIFEST
from photolog.ui.widgets import LogPane

SIM_ROOT = Path.home() / "photolog-sim"


class DevTab(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master)
        self._build()
        self._log_sim_summary()

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)

        banner = ctk.CTkLabel(
            self,
            text="DEV MODE — knobs below mutate global settings. Off in prod builds.",
            fg_color=("#f6dfa0", "#5a4820"),
            corner_radius=6, anchor="w",
        )
        banner.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))

        self._build_throttles(row=1)
        self._build_faults(row=2)
        self._build_fixture(row=3)
        self._build_manifest_tools(row=4)

        ctk.CTkLabel(self, text="Log").grid(row=5, column=0, sticky="w", padx=8, pady=(8, 0))
        self.log = LogPane(self, height=160)
        self.log.grid(row=6, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self.grid_rowconfigure(6, weight=1)

    # ---------- Section 1: throttles ----------

    def _build_throttles(self, *, row: int) -> None:
        outer, frame = _section(self, "Throttles (slow the hot loops to make pause / resume observable)")
        outer.grid(row=row, column=0, sticky="ew", padx=8, pady=4)

        self.copy_delay_var = tk.DoubleVar(value=dev.SETTINGS.copy_chunk_delay_s * 1000)
        self.thumb_delay_var = tk.DoubleVar(value=dev.SETTINGS.thumb_segment_delay_s * 1000)

        _slider_row(
            frame, row=0,
            label="Copy chunk delay (ms/4 MiB)",
            var=self.copy_delay_var,
            from_=0, to=2000,
            on_change=lambda v: self._set_setting("copy_chunk_delay_s", float(v) / 1000.0),
        )
        _slider_row(
            frame, row=1,
            label="Thumb segment delay (ms)",
            var=self.thumb_delay_var,
            from_=0, to=5000,
            on_change=lambda v: self._set_setting("thumb_segment_delay_s", float(v) / 1000.0),
        )

        self.log_chunk_var = tk.BooleanVar(value=dev.SETTINGS.log_every_chunk)
        ctk.CTkCheckBox(
            frame, text="Log every chunk (verbose)",
            variable=self.log_chunk_var,
            command=lambda: self._set_setting("log_every_chunk", self.log_chunk_var.get()),
        ).grid(row=2, column=0, columnspan=3, sticky="w", padx=6, pady=4)

    # ---------- Section 2: faults ----------

    def _build_faults(self, *, row: int) -> None:
        outer, frame = _section(self, "Fault injection (copy index counts both done + skipped files)")
        outer.grid(row=row, column=0, sticky="ew", padx=8, pady=4)

        self.fail_idx_var = tk.StringVar(value="" if dev.SETTINGS.fail_at_copy_index < 0 else str(dev.SETTINGS.fail_at_copy_index))
        self.crash_idx_var = tk.StringVar(value="" if dev.SETTINGS.crash_at_copy_index < 0 else str(dev.SETTINGS.crash_at_copy_index))
        self.fail_seg_var = tk.StringVar(value=dev.SETTINGS.fail_segment_name)

        _entry_row(frame, row=0, label="Fail at copy index", var=self.fail_idx_var,
                   on_set=lambda: self._set_int_setting("fail_at_copy_index", self.fail_idx_var.get()))
        _entry_row(frame, row=1, label="Crash (os._exit) at copy index", var=self.crash_idx_var,
                   on_set=lambda: self._set_int_setting("crash_at_copy_index", self.crash_idx_var.get()))
        _entry_row(frame, row=2, label="Fail segment name (thumb)", var=self.fail_seg_var,
                   on_set=lambda: self._set_setting("fail_segment_name", self.fail_seg_var.get().strip()))

        ctk.CTkButton(
            frame, text="Clear all faults", command=self._clear_faults, width=160,
        ).grid(row=3, column=0, columnspan=3, sticky="w", padx=6, pady=6)

    # ---------- Section 3: sim tree ----------

    def _build_fixture(self, *, row: int) -> None:
        outer, frame = _section(self, f"Sim tree — {SIM_ROOT}")
        outer.grid(row=row, column=0, sticky="ew", padx=8, pady=4)
        frame.grid_columnconfigure(7, weight=1)

        self.year_var = tk.StringVar(value="2023")
        self.seg_count_var = tk.StringVar(value="3")
        self.img_count_var = tk.StringVar(value="5")
        self.size_var = tk.StringVar(value="small (~200 KB)")

        ctk.CTkLabel(frame, text="Year").grid(row=0, column=0, padx=(6, 2), pady=6, sticky="e")
        ctk.CTkEntry(frame, textvariable=self.year_var, width=70).grid(row=0, column=1, padx=2)
        ctk.CTkLabel(frame, text="Segs").grid(row=0, column=2, padx=(10, 2), sticky="e")
        ctk.CTkEntry(frame, textvariable=self.seg_count_var, width=60).grid(row=0, column=3, padx=2)
        ctk.CTkLabel(frame, text="Imgs/seg").grid(row=0, column=4, padx=(10, 2), sticky="e")
        ctk.CTkEntry(frame, textvariable=self.img_count_var, width=60).grid(row=0, column=5, padx=2)
        ctk.CTkLabel(frame, text="Size").grid(row=0, column=6, padx=(10, 2), sticky="e")
        ctk.CTkComboBox(
            frame, values=list(dev_fixtures.SIZE_PRESETS.keys()),
            variable=self.size_var, width=160,
        ).grid(row=0, column=7, padx=2, sticky="w")

        btns = ctk.CTkFrame(frame, fg_color="transparent")
        btns.grid(row=1, column=0, columnspan=8, sticky="ew", padx=4, pady=4)
        ctk.CTkButton(btns, text="Seed", width=100, command=self._seed).pack(side="left", padx=4)
        ctk.CTkButton(
            btns, text="Use sim tree paths", width=160,
            command=self._apply_sim_paths,
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            btns, text="Reset N+H (keep usb)", width=180,
            fg_color="#b85500", hover_color="#933f00",
            command=lambda: self._reset(wipe_usb=False),
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            btns, text="Reset ALL (incl. usb)", width=180,
            fg_color="#b80000", hover_color="#8a0000",
            command=lambda: self._reset(wipe_usb=True),
        ).pack(side="left", padx=4)
        ctk.CTkButton(btns, text="Summary", width=100, command=self._log_sim_summary).pack(side="left", padx=4)

    # ---------- Section 4: manifest tools ----------

    def _build_manifest_tools(self, *, row: int) -> None:
        outer, frame = _section(self, "Manifest tools")
        outer.grid(row=row, column=0, sticky="ew", padx=8, pady=4)

        self.manifest_year_var = tk.StringVar(value="2023")
        ctk.CTkLabel(frame, text="Year").grid(row=0, column=0, padx=(6, 2), pady=6, sticky="e")
        ctk.CTkEntry(frame, textvariable=self.manifest_year_var, width=80).grid(row=0, column=1, padx=2)

        ctk.CTkButton(
            frame, text="Show copy manifest", width=160,
            command=lambda: self._show_manifest("copy"),
        ).grid(row=0, column=2, padx=4)
        ctk.CTkButton(
            frame, text="Show thumb manifest", width=160,
            command=lambda: self._show_manifest("thumb"),
        ).grid(row=0, column=3, padx=4)
        ctk.CTkButton(
            frame, text="Delete copy manifest", width=160,
            fg_color="#b85500", hover_color="#933f00",
            command=self._delete_copy_manifest,
        ).grid(row=1, column=2, padx=4, pady=4)
        ctk.CTkButton(
            frame, text="Delete .partial sidecars", width=200,
            fg_color="#b85500", hover_color="#933f00",
            command=self._delete_partials,
        ).grid(row=1, column=3, padx=4, pady=4)

    # ---------- helpers ----------

    def _set_setting(self, name: str, value) -> None:
        setattr(dev.SETTINGS, name, value)
        self._log(f"{name} = {value!r}")

    def _set_int_setting(self, name: str, raw: str) -> None:
        raw = raw.strip()
        value = -1 if not raw else int(raw)
        setattr(dev.SETTINGS, name, value)
        self._log(f"{name} = {value}")

    def _clear_faults(self) -> None:
        dev.SETTINGS.fail_at_copy_index = -1
        dev.SETTINGS.crash_at_copy_index = -1
        dev.SETTINGS.fail_segment_name = ""
        self.fail_idx_var.set("")
        self.crash_idx_var.set("")
        self.fail_seg_var.set("")
        self._log("All faults cleared.")

    def _seed(self) -> None:
        try:
            year = int(self.year_var.get())
            segs = int(self.seg_count_var.get())
            imgs = int(self.img_count_var.get())
        except ValueError:
            messagebox.showerror("Dev", "Year / Segs / Imgs must be integers.")
            return
        try:
            gm = paths.gm_executable()
        except RuntimeError as e:
            messagebox.showerror("Dev", f"gm not configured: {e}")
            return
        size = self.size_var.get()
        self._log(f"Seeding {segs} segs × {imgs} imgs @ {size} into {SIM_ROOT}/usb/{year}…")

        def worker():
            try:
                report = dev_fixtures.seed_sim_tree(
                    SIM_ROOT, year=year, segments=segs,
                    imgs_per_seg=imgs, size_preset=size, gm=gm,
                )
                self.after(0, lambda: self._log(
                    f"✓ Seeded {report.images} images, "
                    f"{report.total_bytes / 1e6:.1f} MB total."
                ))
                self.after(0, self._log_sim_summary)
            except Exception as e:  # noqa: BLE001
                self.after(0, lambda: self._log(f"ERROR seeding: {e}"))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_sim_paths(self) -> None:
        try:
            gm = paths.gm_executable()
        except RuntimeError:
            # gm not in config yet — use auto-detect
            from photolog.core.dev_fixtures import sim_machine_config
            from pathlib import Path as _Path
            import shutil as _shutil
            gm_candidates = ["/opt/homebrew/bin/gm", "/usr/local/bin/gm", "/usr/bin/gm"]
            gm = next((_Path(p) for p in gm_candidates if _Path(p).exists()), None)
            if gm is None:
                messagebox.showerror("Dev", "Can't find gm executable. Set PHOTOLOG_GM or install graphicsmagick.")
                return
        try:
            dev_fixtures.apply_sim_config(SIM_ROOT, gm)
        except dev_fixtures.UnsafeRootError as e:
            messagebox.showerror("Dev", str(e))
            return
        self._log(f"Configured app paths → {SIM_ROOT}/N   (gm: {gm})")
        self._log("Restart the app or open Settings → Save to pick up new paths in the tabs.")

    def _reset(self, *, wipe_usb: bool) -> None:
        prompt = "Delete N/ and H/ under the sim root?"
        if wipe_usb:
            prompt = "Delete N/, H/, AND usb/ under the sim root? (re-seeding required)"
        if not messagebox.askyesno("Dev reset", prompt):
            return
        try:
            removed = dev_fixtures.reset_sim_tree(SIM_ROOT, wipe_usb=wipe_usb)
        except dev_fixtures.UnsafeRootError as e:
            messagebox.showerror("Dev", str(e))
            return
        for p in removed:
            self._log(f"Removed {p}")
        if not removed:
            self._log("Nothing to remove.")
        self._log_sim_summary()

    def _log_sim_summary(self) -> None:
        self._log(f"sim: {dev_fixtures.tree_summary(SIM_ROOT)}")

    def _show_manifest(self, kind: str) -> None:
        y = self.manifest_year_var.get().strip()
        if not y.isdigit():
            self._log("Year must be numeric.")
            return
        year = int(y)
        try:
            if kind == "copy":
                path = paths.year_dest_root(year) / COPY_MANIFEST
            else:
                path = paths.year_tn_root(year) / THUMB_MANIFEST
        except RuntimeError as e:
            self._log(f"Not configured: {e}")
            return
        if not path.exists():
            self._log(f"No {kind} manifest at {path}")
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            self._log(f"Read error: {e}")
            return
        self._log(f"--- {path} ---")
        summary = {k: v for k, v in data.items() if k not in ("files",)}
        summary["files_count"] = len(data.get("files", []))
        self._log(json.dumps(summary, indent=2))

    def _delete_copy_manifest(self) -> None:
        y = self.manifest_year_var.get().strip()
        if not y.isdigit():
            return
        try:
            path = paths.year_dest_root(int(y)) / COPY_MANIFEST
        except RuntimeError as e:
            self._log(str(e)); return
        if path.exists():
            path.unlink()
            self._log(f"Deleted {path}")
        else:
            self._log(f"No manifest at {path}")

    def _delete_partials(self) -> None:
        y = self.manifest_year_var.get().strip()
        if not y.isdigit():
            return
        try:
            root = paths.year_dest_root(int(y))
        except RuntimeError as e:
            self._log(str(e)); return
        if not root.exists():
            self._log(f"{root} doesn't exist.")
            return
        count = 0
        for p in root.rglob("*" + PARTIAL_SUFFIX):
            try:
                p.unlink()
                count += 1
            except OSError:
                pass
        self._log(f"Removed {count} .partial sidecar(s) under {root}")

    def _log(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.log.append(f"{ts}  {msg}")


# --------------------------------------------------------------------------- #
# Small widget helpers
# --------------------------------------------------------------------------- #

def _section(master, title: str) -> tuple[ctk.CTkFrame, ctk.CTkFrame]:
    """Return (outer, inner). Caller grids outer onto master; adds widgets to inner."""
    outer = ctk.CTkFrame(master)
    outer.grid_columnconfigure(0, weight=1)
    ctk.CTkLabel(outer, text=title, anchor="w",
                 font=ctk.CTkFont(size=12, weight="bold")).grid(
        row=0, column=0, columnspan=8, sticky="w", padx=6, pady=(4, 2))
    inner = ctk.CTkFrame(outer, fg_color="transparent")
    inner.grid(row=1, column=0, columnspan=8, sticky="ew", padx=2, pady=(0, 4))
    inner.grid_columnconfigure(1, weight=1)
    return outer, inner


def _slider_row(master, *, row: int, label: str, var: tk.Variable, from_: float, to: float, on_change) -> None:
    ctk.CTkLabel(master, text=label, anchor="w").grid(row=row, column=0, sticky="w", padx=6, pady=2)
    slider = ctk.CTkSlider(master, from_=from_, to=to, variable=var, command=on_change)
    slider.grid(row=row, column=1, sticky="ew", padx=6)
    val_lbl = ctk.CTkLabel(master, textvariable=var, width=60, anchor="e")
    val_lbl.grid(row=row, column=2, sticky="e", padx=4)


def _entry_row(master, *, row: int, label: str, var: tk.Variable, on_set) -> None:
    ctk.CTkLabel(master, text=label, anchor="w").grid(row=row, column=0, sticky="w", padx=6, pady=2)
    entry = ctk.CTkEntry(master, textvariable=var, width=180)
    entry.grid(row=row, column=1, sticky="w", padx=6)
    ctk.CTkButton(master, text="Apply", width=70, command=on_set).grid(row=row, column=2, padx=4)
