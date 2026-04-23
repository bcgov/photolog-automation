"""Shared preflight confirmation card.

Both tabs run the user's picked folder through a detector (`copy_detect` /
`thumb_detect`) and get back either an interpretation or a Refusal. The card
renders whichever it's given and gates the job behind an explicit "Start" click.

The widget is intentionally dumb: it knows how to render rows of (label,
value), a notes block, and two buttons. The tab decides what rows to show.
"""
from __future__ import annotations

from typing import Callable, Iterable

import customtkinter as ctk


class PreflightCard(ctk.CTkFrame):
    """Key/value panel + optional notes + Start / Pick different buttons.

    Lifecycle:
        card = PreflightCard(parent, on_start=..., on_pick_different=...)
        card.grid(...)
        # Either:
        card.show_interpretation(
            title="Copy — 2023 (year container)",
            rows=[("Source", "/Volumes/…/2023"), ("Dest", "N:\\RPM\\2023"), …],
            notes=["Auto-descended into '2023 Highway Images'."],
            start_label="Start copy",
            warnings=[],
        )
        # Or:
        card.show_refusal("Pick the Highway Images folder, not a drive root.")
        # Or:
        card.clear()
    """

    def __init__(
        self,
        master,
        on_start: Callable[[], None],
        on_pick_different: Callable[[], None],
    ):
        super().__init__(master, corner_radius=8)
        self._on_start = on_start
        self._on_pick_different = on_pick_different
        self._build()
        self.clear()

    # ---------- build ----------

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)

        self._title = ctk.CTkLabel(
            self, text="", anchor="w", font=ctk.CTkFont(size=14, weight="bold")
        )
        self._title.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 2))

        self._banner = ctk.CTkLabel(
            self,
            text="",
            anchor="w",
            wraplength=560,
            justify="left",
            fg_color=("#f6c6c6", "#5c2a2a"),
            corner_radius=6,
        )
        # Stays hidden until show_refusal() or show_interpretation(warnings=…).

        self._rows_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._rows_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=2)
        self._rows_frame.grid_columnconfigure(1, weight=1)
        self._row_widgets: list[tuple[ctk.CTkLabel, ctk.CTkLabel]] = []

        self._notes = ctk.CTkLabel(
            self, text="", anchor="w", wraplength=560, justify="left",
            text_color=("#555", "#bbb"),
        )
        # Packed on demand.

        self._btn_row = ctk.CTkFrame(self, fg_color="transparent")
        self._btn_row.grid(row=4, column=0, sticky="ew", padx=8, pady=(6, 8))
        self._start_btn = ctk.CTkButton(
            self._btn_row, text="Start", width=140, command=self._on_start
        )
        self._start_btn.pack(side="left", padx=4)
        self._pick_btn = ctk.CTkButton(
            self._btn_row,
            text="Pick different…",
            width=140,
            fg_color="transparent",
            border_width=1,
            command=self._on_pick_different,
        )
        self._pick_btn.pack(side="left", padx=4)

    # ---------- public state transitions ----------

    def clear(self) -> None:
        """Hide everything — no interpretation yet, nothing to confirm."""
        self._title.configure(text="No source selected.")
        self._banner.grid_forget()
        self._notes.grid_forget()
        self._clear_rows()
        self._start_btn.configure(state="disabled")
        self._pick_btn.configure(state="disabled")

    def show_refusal(self, reason: str) -> None:
        self._title.configure(text="Can't use this folder")
        self._banner.configure(text=reason)
        self._banner.grid(row=1, column=0, sticky="ew", padx=10, pady=6)
        self._notes.grid_forget()
        self._clear_rows()
        self._start_btn.configure(state="disabled")
        self._pick_btn.configure(state="normal")

    def show_interpretation(
        self,
        title: str,
        rows: Iterable[tuple[str, str]],
        *,
        notes: Iterable[str] = (),
        warnings: Iterable[str] = (),
        start_label: str = "Start",
        start_enabled: bool = True,
    ) -> None:
        self._title.configure(text=title)

        warning_text = "\n".join(w for w in warnings if w)
        if warning_text:
            self._banner.configure(text=warning_text)
            self._banner.grid(row=1, column=0, sticky="ew", padx=10, pady=6)
        else:
            self._banner.grid_forget()

        self._render_rows(list(rows))

        note_text = "\n".join(n for n in notes if n)
        if note_text:
            self._notes.configure(text=note_text)
            self._notes.grid(row=3, column=0, sticky="ew", padx=10, pady=(2, 4))
        else:
            self._notes.grid_forget()

        self._start_btn.configure(text=start_label, state="normal" if start_enabled else "disabled")
        self._pick_btn.configure(state="normal")

    def set_start_enabled(self, enabled: bool) -> None:
        self._start_btn.configure(state="normal" if enabled else "disabled")

    # ---------- internals ----------

    def _clear_rows(self) -> None:
        for lbl, val in self._row_widgets:
            lbl.destroy()
            val.destroy()
        self._row_widgets = []

    def _render_rows(self, rows: list[tuple[str, str]]) -> None:
        self._clear_rows()
        for i, (label, value) in enumerate(rows):
            lbl = ctk.CTkLabel(
                self._rows_frame, text=f"{label}:", anchor="ne",
                text_color=("#444", "#aaa"),
            )
            lbl.grid(row=i, column=0, sticky="ne", padx=(0, 8), pady=2)
            val = ctk.CTkLabel(
                self._rows_frame, text=value, anchor="w", justify="left",
                wraplength=420,
            )
            val.grid(row=i, column=1, sticky="w", pady=2)
            self._row_widgets.append((lbl, val))
