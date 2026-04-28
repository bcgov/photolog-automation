"""Photolog main application window."""
from __future__ import annotations

import argparse
import os
import sys

try:
    import customtkinter as ctk
except ImportError as exc:
    if getattr(exc, "name", None) == "_tkinter" or "_tkinter" in str(exc):
        py_minor = f"{sys.version_info.major}.{sys.version_info.minor}"
        raise SystemExit(
            "Photolog needs Tkinter, but this Python install does not include _tkinter.\n"
            "Install the Tk package that matches the Python used by the virtualenv.\n"
            f"Homebrew example: brew install python-tk@{py_minor}\n"
            "Then recreate the virtualenv and run: python -m photolog"
        ) from exc
    raise

from pathlib import Path

from photolog import __version__
from photolog.core import dev, paths
from photolog.core.fs import DiskStats
from photolog.ui.copy_tab import CopyTab
from photolog.ui.full_tab import FullTab
from photolog.ui.setup_dialog import SetupDialog
from photolog.ui.thumb_tab import ThumbTab
from photolog.ui.widgets import StatsFooter


class PhotologApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"Photolog {__version__}")
        self.geometry("960x700")
        self.minsize(820, 560)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=8, pady=(8, 0))
        ctk.CTkLabel(header, text=f"Photolog  v{__version__}", anchor="w").pack(side="left")
        ctk.CTkButton(
            header, text="Settings…", width=100, command=self._open_setup
        ).pack(side="right")

        self._tabs = ctk.CTkTabview(self)
        self._tabs.pack(fill="both", expand=True, padx=8, pady=(6, 0))
        self._tabs.add("Full ingest")
        self._tabs.add("Copy from USB")
        self._tabs.add("Generate Thumbnails")

        self.full_tab = FullTab(self._tabs.tab("Full ingest"))
        self.full_tab.pack(fill="both", expand=True)
        self.copy_tab = CopyTab(self._tabs.tab("Copy from USB"))
        self.copy_tab.pack(fill="both", expand=True)
        self.thumb_tab = ThumbTab(self._tabs.tab("Generate Thumbnails"))
        self.thumb_tab.pack(fill="both", expand=True)

        # Dev tab is lazy-imported + only added when the process opted in.
        # The module is stripped from prod PyInstaller builds via --exclude-module,
        # so importing it in a shipped binary would fail — the guard makes sure
        # we never try.
        if dev.is_dev_enabled():
            self._tabs.add("Dev")
            from photolog.ui.dev_tab import DevTab
            DevTab(self._tabs.tab("Dev")).pack(fill="both", expand=True)
            self.title(f"Photolog {__version__}  [DEV]")

        self.footer = StatsFooter(self)
        self.footer.pack(fill="x", padx=8, pady=(0, 6))

        self._refresh_stats()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        # First-run setup: open the dialog after the main loop is up so the
        # Toplevel can parent correctly.
        if not paths.is_configured():
            self.after(100, self._open_setup)

    # ---------- shutdown ----------

    def _on_close(self) -> None:
        """Cancel any running jobs so they can flush their manifests, then quit."""
        self.full_tab._cancel.set()
        self.copy_tab._cancel.set()
        self.thumb_tab._cancel.set()
        # Give each worker up to 2 s to flush; daemon threads won't block exit.
        for tab in (self.full_tab, self.copy_tab, self.thumb_tab):
            w = getattr(tab, "_worker", None)
            if w and w.is_alive():
                w.join(timeout=2.0)
        self.destroy()

    # ---------- setup / settings ----------

    def _open_setup(self) -> None:
        SetupDialog(self, on_saved=self._on_config_saved)

    def _on_config_saved(self) -> None:
        # Tabs cache nothing that survives a config change except widget vars;
        # pulling fresh values on the next refresh is enough.
        self.full_tab.refresh_from_config()
        self.copy_tab.refresh_from_config()
        self.thumb_tab.refresh_from_config()

    # ---------- footer ----------

    def _refresh_stats(self) -> None:
        try:
            n = _safe_stats_for(paths.hr_root) if paths.is_configured() else DiskStats.missing()
            h = _safe_stats_for(paths.hr_link_root) if paths.is_configured() else DiskStats.missing()
            src = None
            src_str = self.copy_tab.source_var.get().strip() if hasattr(self.copy_tab, "source_var") else ""
            if src_str:
                p = Path(src_str)
                if p.exists():
                    src = p
            self.footer.update_stats(n, h, src)
        finally:
            self.after(3000, self._refresh_stats)


def _safe_stats_for(getter) -> DiskStats:
    try:
        return DiskStats.for_path(getter())
    except Exception:  # noqa: BLE001
        return DiskStats.missing()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="photolog")
    parser.add_argument(
        "--dev", action="store_true",
        help="Enable dev tab (throttles, fault injection, sim-tree fixtures).",
    )
    args = parser.parse_args(argv)
    if args.dev:
        os.environ["PHOTOLOG_DEV"] = "1"

    ctk.set_appearance_mode("System")
    ctk.set_default_color_theme("blue")
    app = PhotologApp()
    app.mainloop()


if __name__ == "__main__":
    main(sys.argv[1:])
