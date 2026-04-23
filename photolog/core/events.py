"""Progress event types shared by workers and UI."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


JobPhase = Literal["scanning", "copying", "hashing", "verifying", "linking", "done", "error", "cancelled", "paused"]


@dataclass
class ProgressEvent:
    phase: JobPhase
    message: str = ""
    # Counts
    files_total: int = 0
    files_done: int = 0
    files_skipped: int = 0
    files_failed: int = 0
    # Bytes
    bytes_total: int = 0
    bytes_done: int = 0
    # Throughput
    bytes_per_sec: float = 0.0
    files_per_sec: float = 0.0
    eta_seconds: float = float("nan")
    # Extras
    current_file: str = ""
    warnings: list[str] = field(default_factory=list)
