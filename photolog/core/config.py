"""Per-machine config file.

Resolution order everywhere else in the app:
    env var override -> this config -> auto-detect default -> prompt

Config file shape:
    {
      "machines": {
        "<hostname>": { ...MachineConfig fields... }
      }
    }

Keyed by hostname so one binary serves both the Mac dev loop and the
Windows Server host(s) without re-running Setup every time.
"""
from __future__ import annotations

import json
import os
import socket
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class MachineConfig:
    hr_root: str = ""           # e.g. N:\RPM    — year folders live under here
    tn_root: str = ""           # e.g. N:\RPM\TN — thumbnail year folders live under here
    hr_link_root: str = ""      # e.g. H:\PLGwww\hr
    tn_link_root: str = ""      # e.g. H:\PLGwww\TN
    gm_exe: str = ""            # e.g. N:\RPM\TN\GraphicsMagick-1.3.23-Q8\gm.exe
    notify_sender: str = ""     # From: address for job notification emails (optional)
    notify_recipient: str = ""  # To: address for job notification emails (optional)

    def is_complete(self) -> bool:
        # Notify fields are optional — leaving them blank disables email.
        return all([self.hr_root, self.tn_root, self.hr_link_root, self.tn_link_root, self.gm_exe])


@dataclass
class AppConfig:
    machines: dict[str, MachineConfig] = field(default_factory=dict)

    def for_current_machine(self) -> MachineConfig:
        return self.machines.get(current_machine_key(), MachineConfig())

    def set_current_machine(self, mc: MachineConfig) -> None:
        self.machines[current_machine_key()] = mc


def current_machine_key() -> str:
    """Short, filesystem-safe hostname key."""
    try:
        return socket.gethostname() or "unknown-host"
    except OSError:
        return "unknown-host"


def config_path() -> Path:
    """Location of the config file.

    Windows: %APPDATA%\\Photolog\\config.json
    macOS / other: $XDG_CONFIG_HOME/photolog/config.json or ~/.config/photolog/config.json
    Overridable via $PHOTOLOG_CONFIG for tests/dev.
    """
    override = os.environ.get("PHOTOLOG_CONFIG")
    if override:
        return Path(override)
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(appdata) / "Photolog" / "config.json"
    xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(xdg) / "photolog" / "config.json"


def load_config() -> AppConfig:
    path = config_path()
    if not path.exists():
        return AppConfig()
    try:
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return AppConfig()
    if not isinstance(raw, dict):
        return AppConfig()
    machines_raw = raw.get("machines") or {}
    machines: dict[str, MachineConfig] = {}
    if isinstance(machines_raw, dict):
        for k, v in machines_raw.items():
            if isinstance(v, dict):
                machines[str(k)] = MachineConfig(
                    hr_root=str(v.get("hr_root", "")),
                    tn_root=str(v.get("tn_root", "")),
                    hr_link_root=str(v.get("hr_link_root", "")),
                    tn_link_root=str(v.get("tn_link_root", "")),
                    gm_exe=str(v.get("gm_exe", "")),
                    notify_sender=str(v.get("notify_sender", "")),
                    notify_recipient=str(v.get("notify_recipient", "")),
                )
    return AppConfig(machines=machines)


def save_config(cfg: AppConfig) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"machines": {k: asdict(v) for k, v in cfg.machines.items()}}
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    os.replace(tmp, path)
