"""Email notifications via the BC government internal SMTP relay.

Host/port are deliberately baked in (apps.smtp.gov.bc.ca:25, no auth, no TLS).
Sender and recipient come from MachineConfig — if either is blank, we silently
skip sending. All sends run on a daemon thread so a slow or unreachable relay
can never stall the worker. Failures are logged to stderr and swallowed; an
SMTP outage must not tank an ingest job.
"""
from __future__ import annotations

import socket
import smtplib
import sys
import threading
from dataclasses import dataclass
from email.message import EmailMessage

from photolog.core import paths
from photolog.core.fs import human_bytes, human_duration

SMTP_HOST = "apps.smtp.gov.bc.ca"
SMTP_PORT = 25
SMTP_TIMEOUT_S = 15.0


@dataclass
class JobReport:
    """Final stats handed to send_finished()."""
    files_total: int = 0
    files_done: int = 0
    files_skipped: int = 0
    files_failed: int = 0
    bytes_total: int = 0
    bytes_done: int = 0
    duration_s: float = 0.0
    outcome: str = "ok"     # ok | cancelled | error
    error: str = ""

    @property
    def success_rate_pct(self) -> float:
        # Skipped files are pre-existing successes, count them in the numerator.
        attempted = self.files_done + self.files_failed
        if attempted == 0:
            return 100.0 if self.files_skipped >= 0 else 0.0
        return 100.0 * self.files_done / attempted


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def send_started(
    *,
    job_kind: str,
    year: int | None,
    source: str,
    dest: str,
    files_total: int = 0,
    bytes_total: int = 0,
    segments: int | None = None,
) -> None:
    sender, recipient = _addresses()
    if not sender or not recipient:
        return
    subject = f"[Photolog] {job_kind} started — {year if year else 'unknown year'} on {_host()}"
    body = _format_started(
        job_kind=job_kind, year=year, source=source, dest=dest,
        files_total=files_total, bytes_total=bytes_total, segments=segments,
    )
    _dispatch(sender, recipient, subject, body)


def send_finished(
    *,
    job_kind: str,
    year: int | None,
    source: str,
    dest: str,
    report: JobReport,
    segments: int | None = None,
) -> None:
    sender, recipient = _addresses()
    if not sender or not recipient:
        return
    tag = {"ok": "completed", "cancelled": "cancelled", "error": "FAILED"}.get(report.outcome, report.outcome)
    subject = f"[Photolog] {job_kind} {tag} — {year if year else 'unknown year'} on {_host()}"
    body = _format_finished(
        job_kind=job_kind, year=year, source=source, dest=dest,
        report=report, segments=segments,
    )
    _dispatch(sender, recipient, subject, body)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _addresses() -> tuple[str, str]:
    cfg = paths.effective_config()
    return cfg.notify_sender.strip(), cfg.notify_recipient.strip()


def _host() -> str:
    try:
        return socket.gethostname() or "unknown-host"
    except OSError:
        return "unknown-host"


def _dispatch(sender: str, recipient: str, subject: str, body: str) -> None:
    t = threading.Thread(
        target=_send,
        args=(sender, recipient, subject, body),
        name="photolog-notify",
        daemon=True,
    )
    t.start()


def _send(sender: str, recipient: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT_S) as smtp:
            smtp.send_message(msg)
    except (OSError, smtplib.SMTPException) as e:
        print(f"[photolog notify] send failed ({SMTP_HOST}:{SMTP_PORT}): {e}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Body formatters — plain text, monospace-friendly, no HTML
# ---------------------------------------------------------------------------

_RULE = "-" * 60


def _format_started(
    *, job_kind: str, year: int | None, source: str, dest: str,
    files_total: int, bytes_total: int, segments: int | None,
) -> str:
    lines = [
        f"Photolog — {job_kind} started",
        _RULE,
        f"  Host        : {_host()}",
        f"  Year        : {year if year else '(unknown)'}",
        f"  Source      : {source}",
        f"  Destination : {dest}",
    ]
    if segments is not None:
        lines.append(f"  Segments    : {segments}")
    if files_total:
        lines.append(f"  Files       : {files_total:,}")
    if bytes_total:
        lines.append(f"  Total size  : {human_bytes(bytes_total)}")
    lines += [
        _RULE,
        "A second email will be sent when the job finishes.",
    ]
    return "\n".join(lines) + "\n"


def _format_finished(
    *, job_kind: str, year: int | None, source: str, dest: str,
    report: JobReport, segments: int | None,
) -> str:
    outcome_label = {
        "ok": "OK — completed successfully",
        "cancelled": "CANCELLED by user",
        "error": "FAILED — see error below",
    }.get(report.outcome, report.outcome.upper())

    lines = [
        f"Photolog — {job_kind} report",
        _RULE,
        f"  Host        : {_host()}",
        f"  Year        : {year if year else '(unknown)'}",
        f"  Source      : {source}",
        f"  Destination : {dest}",
    ]
    if segments is not None:
        lines.append(f"  Segments    : {segments}")
    lines += [
        f"  Outcome     : {outcome_label}",
        f"  Duration    : {human_duration(report.duration_s)}",
        _RULE,
        "  File totals",
        f"    Total       : {report.files_total:,}",
        f"    Succeeded   : {report.files_done:,}",
        f"    Skipped     : {report.files_skipped:,}   (already complete / pre-existing)",
        f"    Failed      : {report.files_failed:,}",
        f"    Success rate: {report.success_rate_pct:.1f}%",
    ]
    if report.bytes_total:
        lines += [
            _RULE,
            "  Bytes",
            f"    Transferred : {human_bytes(report.bytes_done)} / {human_bytes(report.bytes_total)}",
        ]
    if report.error:
        lines += [
            _RULE,
            "  Error",
            "    " + report.error.replace("\n", "\n    "),
        ]
    lines.append(_RULE)
    return "\n".join(lines) + "\n"
