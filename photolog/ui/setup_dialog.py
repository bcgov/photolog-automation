"""First-run (and re-openable) Setup dialog.

Asks for five paths: HR root, TN root, HR-link root, TN-link root, gm.exe.
Prefills from auto-detected candidates. Saves under the current hostname
so one binary can serve multiple machines.
"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Callable

import customtkinter as ctk

from photolog.core import notify, paths
from photolog.core.config import MachineConfig, load_config, save_config


class SetupDialog(ctk.CTkToplevel):
    """Modal config editor. Calls `on_saved()` once the user accepts."""

    def __init__(self, master, on_saved: Callable[[], None] | None = None):
        super().__init__(master)
        self.title("Photolog — Setup")
        self.geometry("820x560")
        self.minsize(720, 520)
        self.on_saved = on_saved
        self.transient(master)
        self.grab_set()

        current = paths.effective_config()
        self._vars = {
            "hr_root": tk.StringVar(value=current.hr_root),
            "tn_root": tk.StringVar(value=current.tn_root),
            "hr_link_root": tk.StringVar(value=current.hr_link_root),
            "tn_link_root": tk.StringVar(value=current.tn_link_root),
            "gm_exe": tk.StringVar(value=current.gm_exe),
            "notify_sender": tk.StringVar(value=current.notify_sender),
            "notify_recipient": tk.StringVar(value=current.notify_recipient),
        }

        self._build()
        # Keep TN root in sync with HR root until the user edits TN explicitly.
        self._tn_edited = bool(current.tn_root) and (current.tn_root != str(Path(current.hr_root or "") / "TN"))
        self._vars["hr_root"].trace_add("write", lambda *_: self._maybe_mirror_tn())

    def _build(self) -> None:
        intro = ctk.CTkLabel(
            self,
            text=(
                "Tell Photolog where things live on this machine.\n"
                "These paths are remembered per-hostname in a local config file."
            ),
            justify="left",
        )
        intro.pack(anchor="w", padx=14, pady=(12, 4))

        # Help banner explaining what each field means
        help_text = ctk.CTkLabel(
            self,
            text=(
                "• HR root: where USB payloads are copied (e.g. N:\\RPM).\n"
                "• TN root: where thumbnails are written (auto: <HR root>\\TN).\n"
                "• HR/TN junction roots: where the junctions point FROM (e.g. H:\\PLGwww\\hr and H:\\PLGwww\\TN).\n"
                "• gm.exe: GraphicsMagick executable (e.g. N:\\RPM\\TN\\GraphicsMagick-1.3.23-Q8\\gm.exe)."
            ),
            justify="left",
            text_color=("gray50", "gray70"),
            font=ctk.CTkFont(size=10),
        )
        help_text.pack(anchor="w", padx=14, pady=(0, 8))

        rows = (
            ("HR root (where USB copies go)",       "hr_root",      True,  "N:\\RPM"),
            ("TN root (where thumbnails go)",       "tn_root",      True,  "<HR root>\\TN"),
            ("HR junction root (link parent)",      "hr_link_root", True,  "H:\\PLGwww\\hr"),
            ("TN junction root (link parent)",      "tn_link_root", True,  "H:\\PLGwww\\TN"),
            ("gm.exe path",                         "gm_exe",       False, "N:\\RPM\\TN\\GraphicsMagick...\\gm.exe"),
        )
        form = ctk.CTkFrame(self, fg_color="transparent")
        form.pack(fill="both", expand=True, padx=14, pady=4)
        form.grid_columnconfigure(1, weight=1)

        for row, (label, key, is_dir, hint) in enumerate(rows):
            ctk.CTkLabel(form, text=label + ":").grid(row=row, column=0, sticky="w", padx=4, pady=4)
            entry = ctk.CTkEntry(form, textvariable=self._vars[key], placeholder_text=hint)
            entry.grid(row=row, column=1, sticky="ew", padx=4)
            ctk.CTkButton(
                form, text="Browse…", width=90,
                command=lambda k=key, d=is_dir: self._browse(k, d),
            ).grid(row=row, column=2, padx=4)

        ctk.CTkLabel(
            self,
            text="Email notifications (optional)",
            font=ctk.CTkFont(weight="bold"),
        ).pack(anchor="w", padx=14, pady=(10, 0))
        ctk.CTkLabel(
            self,
            text=(
                f"Sent via {notify.SMTP_HOST}:{notify.SMTP_PORT} (no auth, internal relay).\n"
                "Leave both fields blank to disable notifications."
            ),
            justify="left",
            text_color=("gray50", "gray70"),
            font=ctk.CTkFont(size=10),
        ).pack(anchor="w", padx=14, pady=(0, 4))

        mail = ctk.CTkFrame(self, fg_color="transparent")
        mail.pack(fill="x", padx=14, pady=2)
        mail.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(mail, text="Sender:").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        ctk.CTkEntry(
            mail, textvariable=self._vars["notify_sender"],
            placeholder_text="photolog@gov.bc.ca",
        ).grid(row=0, column=1, sticky="ew", padx=4)
        ctk.CTkLabel(mail, text="Recipient:").grid(row=1, column=0, sticky="w", padx=4, pady=4)
        ctk.CTkEntry(
            mail, textvariable=self._vars["notify_recipient"],
            placeholder_text="ops.team@gov.bc.ca",
        ).grid(row=1, column=1, sticky="ew", padx=4)
        ctk.CTkButton(
            mail, text="Send test", width=90, command=self._send_test,
        ).grid(row=0, column=2, rowspan=2, padx=4)

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=14, pady=12)
        ctk.CTkButton(btns, text="Cancel", command=self.destroy, width=100).pack(side="right", padx=4)
        ctk.CTkButton(btns, text="Save", command=self._save, width=100).pack(side="right", padx=4)

    def _browse(self, key: str, is_dir: bool) -> None:
        current = self._vars[key].get().strip()
        if is_dir:
            picked = filedialog.askdirectory(
                title="Pick folder",
                initialdir=current or None,
                parent=self,
            )
        else:
            picked = filedialog.askopenfilename(
                title="Pick gm.exe",
                initialdir=str(Path(current).parent) if current else None,
                filetypes=[("Executable", "*.exe gm"), ("All files", "*")],
                parent=self,
            )
        if picked:
            self._vars[key].set(picked)
            if key == "tn_root":
                self._tn_edited = True

    def _maybe_mirror_tn(self) -> None:
        if self._tn_edited:
            return
        hr = self._vars["hr_root"].get().strip()
        if hr:
            self._vars["tn_root"].set(str(Path(hr) / "TN"))

    def _send_test(self) -> None:
        sender = self._vars["notify_sender"].get().strip()
        recipient = self._vars["notify_recipient"].get().strip()
        if not sender or not recipient:
            messagebox.showerror(
                "Notifications",
                "Fill in both Sender and Recipient before sending a test.",
                parent=self,
            )
            return
        # Build a one-shot message and reuse the transport synchronously so
        # the user sees the actual SMTP error instead of it disappearing into
        # the background thread.
        from email.message import EmailMessage
        import smtplib
        msg = EmailMessage()
        msg["From"] = sender
        msg["To"] = recipient
        msg["Subject"] = "[Photolog] test email"
        msg.set_content(
            "This is a Photolog test message.\n"
            f"Relay: {notify.SMTP_HOST}:{notify.SMTP_PORT}\n"
            "If you received this, notifications are wired up correctly.\n"
        )
        try:
            with smtplib.SMTP(notify.SMTP_HOST, notify.SMTP_PORT, timeout=notify.SMTP_TIMEOUT_S) as smtp:
                smtp.send_message(msg)
        except (OSError, smtplib.SMTPException) as e:
            messagebox.showerror("Test email failed", f"{type(e).__name__}: {e}", parent=self)
            return
        messagebox.showinfo(
            "Test email sent",
            f"Sent to {recipient}. Check the inbox.",
            parent=self,
        )

    def _save(self) -> None:
        mc = MachineConfig(
            hr_root=self._vars["hr_root"].get().strip(),
            tn_root=self._vars["tn_root"].get().strip(),
            hr_link_root=self._vars["hr_link_root"].get().strip(),
            tn_link_root=self._vars["tn_link_root"].get().strip(),
            gm_exe=self._vars["gm_exe"].get().strip(),
            notify_sender=self._vars["notify_sender"].get().strip(),
            notify_recipient=self._vars["notify_recipient"].get().strip(),
        )
        # Notify fields are optional — exclude them from the "required" check.
        required = {"hr_root", "tn_root", "hr_link_root", "tn_link_root", "gm_exe"}
        missing = [k for k in required if not getattr(mc, k)]
        if missing:
            messagebox.showerror("Setup incomplete", f"Please fill in: {', '.join(missing)}", parent=self)
            return
        if not Path(mc.hr_root).exists():
            if not messagebox.askyesno("HR root not found", f"{mc.hr_root} doesn't exist. Save anyway?", parent=self):
                return
        if not Path(mc.gm_exe).exists():
            if not messagebox.askyesno("gm.exe not found", f"{mc.gm_exe} doesn't exist. Save anyway?", parent=self):
                return

        cfg = load_config()
        cfg.set_current_machine(mc)
        try:
            save_config(cfg)
        except OSError as e:
            messagebox.showerror("Can't save config", str(e), parent=self)
            return
        self.destroy()
        if self.on_saved is not None:
            self.on_saved()
