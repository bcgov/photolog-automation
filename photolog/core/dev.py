"""Dev-mode knobs consumed by the copy/thumb engines.

This module is ALWAYS imported by the jobs; its defaults are inert, so a
production binary incurs one float-compare per chunk. Mutations happen only
from the Dev tab (which itself is only loaded when `--dev` / PHOTOLOG_DEV=1).

The Dev tab, its widget module, and `dev_fixtures` are stripped from the
PyInstaller build via --exclude-module, so even flipping PHOTOLOG_DEV at
runtime in a prod binary cannot surface dev UI.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class DevSettings:
    # Throttles — how long to sleep inside the hot loops.
    copy_chunk_delay_s: float = 0.0
    thumb_segment_delay_s: float = 0.0

    # Fault injection — indexed by _files_done at the moment the hook fires.
    # -1 disables. The hook fires when self._files_done == trigger.
    fail_at_copy_index: int = -1      # raise RuntimeError in _process_entry
    crash_at_copy_index: int = -1     # os._exit(1) in _process_entry
    fail_segment_name: str = ""       # force one mogrify to return non-zero

    # Verbose log lines inside _copy_bytes (every chunk).
    log_every_chunk: bool = False


SETTINGS = DevSettings()


def is_dev_enabled() -> bool:
    """True iff the process was launched with --dev or PHOTOLOG_DEV=1."""
    raw = os.environ.get("PHOTOLOG_DEV", "0").strip()
    if not raw:
        return False
    try:
        return bool(int(raw))
    except ValueError:
        return raw.lower() in ("true", "yes", "on")
