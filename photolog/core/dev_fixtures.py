"""Sim-tree builder + wiper for the Dev tab.

Why in-app instead of a shell script: one click reproduces a known-good
starting state on the machine the developer is already on, using the same
gm.exe the app uses. The shell scripts in prior iterations drifted out of
sync with the app's directory conventions.

Safety: the wiper refuses to touch any directory not named "photolog-sim"
under the current user's home. This is a hard refusal, not an assertion —
we raise `UnsafeRootError` so the UI surfaces a clear message.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Size knob → (width, height, quality) chosen to land near the requested MB.
# gm convert xc:rgb(...) produces highly compressible output; bumping quality
# is the knob that actually grows the file.
SIZE_PRESETS: dict[str, tuple[int, int, int]] = {
    "small (~200 KB)":  (1280, 960, 85),
    "medium (~1 MB)":   (2400, 1800, 92),
    "large (~5 MB)":    (4800, 3600, 95),
}


class UnsafeRootError(Exception):
    """Raised when someone asks us to wipe a suspicious root."""


@dataclass
class SeedReport:
    segments: int
    images: int
    total_bytes: int
    sim_root: Path


def _ensure_safe_sim_root(root: Path) -> None:
    """Refuse to operate on anything that isn't ~/photolog-sim."""
    resolved = root.resolve(strict=False)
    home = Path.home().resolve(strict=False)
    if resolved.name != "photolog-sim":
        raise UnsafeRootError(f"Refusing: {resolved} is not named 'photolog-sim'.")
    try:
        resolved.relative_to(home)
    except ValueError as e:
        raise UnsafeRootError(f"Refusing: {resolved} is not inside {home}.") from e


def sim_machine_config(root: Path, gm: Path) -> "MachineConfig":
    """Return a MachineConfig wired to the sim tree under `root`."""
    from photolog.core.config import MachineConfig  # local import — avoids circular at module level
    n = root / "N"
    h = root / "H"
    return MachineConfig(
        hr_root=str(n),
        tn_root=str(n / "TN"),
        hr_link_root=str(h / "hr"),
        tn_link_root=str(h / "TN"),
        gm_exe=str(gm),
    )


def apply_sim_config(root: Path, gm: Path) -> None:
    """Write config.json so the app's paths point at the sim tree."""
    from photolog.core.config import load_config, save_config
    _ensure_safe_sim_root(root)
    mc = sim_machine_config(root, gm)
    # Create the output directories so auto-detect finds them next boot.
    for d in (root / "N", root / "N" / "TN", root / "H" / "hr", root / "H" / "TN"):
        d.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    cfg.set_current_machine(mc)
    save_config(cfg)


def seed_sim_tree(
    root: Path,
    *,
    year: int,
    segments: int,
    imgs_per_seg: int,
    size_preset: str,
    gm: Path,
) -> SeedReport:
    """Create a realistic sim USB payload using real gm-generated JPEGs."""
    _ensure_safe_sim_root(root)
    if size_preset not in SIZE_PRESETS:
        raise ValueError(f"Unknown size preset {size_preset!r}")
    if not gm.exists():
        raise FileNotFoundError(f"gm executable not found: {gm}")

    # Pre-create N/H output dirs so paths.py auto-detect finds them.
    for d in (root / "N", root / "N" / "TN", root / "H" / "hr", root / "H" / "TN"):
        d.mkdir(parents=True, exist_ok=True)

    w, h, quality = SIZE_PRESETS[size_preset]
    usb = root / "usb" / str(year) / f"{year} Highway Images"
    usb.mkdir(parents=True, exist_ok=True)

    total_bytes = 0
    images = 0
    for s in range(segments):
        seg_name = f"H1_E_00_km{s * 100:04d}"
        seg_dir = usb / seg_name
        seg_dir.mkdir(parents=True, exist_ok=True)
        for i in range(imgs_per_seg):
            dst = seg_dir / f"img{i:04d}.jpg"
            if dst.exists():
                continue
            # Vary color per image so each JPEG is unique — otherwise gm would
            # produce byte-identical outputs and mask real hash-compare bugs.
            r = (s * 47 + i * 11) % 256
            g = (s * 83 + i * 29) % 256
            b = (s * 31 + i * 53) % 256
            subprocess.run(
                [
                    str(gm), "convert",
                    "-size", f"{w}x{h}",
                    f"xc:rgb({r},{g},{b})",
                    "-quality", str(quality),
                    str(dst),
                ],
                check=True, capture_output=True,
            )
            total_bytes += dst.stat().st_size
            images += 1
    return SeedReport(segments=segments, images=images, total_bytes=total_bytes, sim_root=root)


def reset_sim_tree(root: Path, *, wipe_usb: bool = False) -> list[Path]:
    """Delete N/, H/, and optionally usb/ under the sim root. Returns what was removed."""
    _ensure_safe_sim_root(root)
    removed: list[Path] = []
    targets = [root / "N", root / "H"]
    if wipe_usb:
        targets.append(root / "usb")
    for t in targets:
        if t.exists():
            shutil.rmtree(t)
            removed.append(t)
    return removed


def tree_summary(root: Path) -> str:
    """One-line stat block describing the current sim tree."""
    try:
        _ensure_safe_sim_root(root)
    except UnsafeRootError as e:
        return str(e)
    parts = []
    for label, sub in (("usb", "usb"), ("N", "N"), ("H", "H")):
        p = root / sub
        if not p.exists():
            parts.append(f"{label}=—")
            continue
        files = sum(1 for _ in p.rglob("*") if _.is_file())
        size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
        parts.append(f"{label}={files}f/{size / 1e6:.1f} MB")
    return "   ".join(parts)
