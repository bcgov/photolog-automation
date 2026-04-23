"""Reusable UI widgets."""
from __future__ import annotations

import customtkinter as ctk
from pathlib import Path

from photolog.core.fs import DiskStats, human_bytes


class LogPane(ctk.CTkFrame):
    def __init__(self, master, height: int = 180):
        super().__init__(master)
        self.textbox = ctk.CTkTextbox(self, height=height, activate_scrollbars=True)
        self.textbox.pack(fill="both", expand=True, padx=4, pady=4)
        self.textbox.configure(state="disabled")

    def append(self, line: str) -> None:
        self.textbox.configure(state="normal")
        self.textbox.insert("end", line.rstrip() + "\n")
        self.textbox.see("end")
        self.textbox.configure(state="disabled")


class StatsFooter(ctk.CTkFrame):
    """Persistent footer showing disk usage for N:, H:, and source drive."""

    def __init__(self, master):
        super().__init__(master, height=28)
        self.label = ctk.CTkLabel(self, text="", anchor="w")
        self.label.pack(fill="x", padx=8, pady=4)

    def update_stats(self, n: DiskStats, h: DiskStats, src: Path | None) -> None:
        parts: list[str] = []
        parts.append(_fmt_drive("N", n))
        parts.append(_fmt_drive("H", h))
        if src is not None:
            parts.append(f"src: {src}")
        self.label.configure(text="   •   ".join(parts))


def _fmt_drive(label: str, d: DiskStats) -> str:
    if not d.exists:
        return f"{label}: (missing)"
    return f"{label}: {human_bytes(d.free)} free / {human_bytes(d.total)}"


class ProgressBlock(ctk.CTkFrame):
    """Progress bar + metrics row + status line."""

    def __init__(self, master):
        super().__init__(master)
        self.status_var = ctk.StringVar(value="Idle")
        self.metrics_var = ctk.StringVar(value="")
        ctk.CTkLabel(self, textvariable=self.status_var, anchor="w").pack(fill="x", padx=8, pady=(6, 0))
        self.bar = ctk.CTkProgressBar(self)
        self.bar.set(0)
        self.bar.pack(fill="x", padx=8, pady=4)
        ctk.CTkLabel(self, textvariable=self.metrics_var, anchor="w").pack(fill="x", padx=8, pady=(0, 6))

    def set_status(self, text: str) -> None:
        self.status_var.set(text)

    def set_metrics(self, text: str) -> None:
        self.metrics_var.set(text)

    def set_fraction(self, f: float) -> None:
        self.bar.set(max(0.0, min(1.0, f)))
