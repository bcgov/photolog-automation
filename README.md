# Photolog

Yearly USB → `N:\RPMS$` ingest + thumbnail tool for Windows Server.

## What it does

1. **Copy from USB** → `N:\RPMS$\<year>\` with resumable progress (safe to kill mid-copy).
2. **Generate thumbnails** (320×254, q75) of `<year> Highway Images\*` into `N:\RPMS$\TN\<year>\` via GraphicsMagick.
3. Auto-creates the junctions `H:\PLGwww\hr\<year>` and `H:\PLGwww\TN\<year>` on successful completion of each step.

Both steps are independent tabs; re-running either is idempotent.

## Target platform

Windows Server 2016 / 2022 Standard, x86-64. Ships as a single `Photolog.exe` with Python runtime bundled (PyInstaller `--onefile --windowed`).

## Build

The Windows `.exe` is built by GitHub Actions (`.github/workflows/build.yml`) on `windows-latest` with Python 3.11 x64. Tagging `vX.Y.Z` attaches the binary to a GitHub Release. PyInstaller cannot cross-compile; local Mac builds are not distributable.

## Dev loop (macOS)

```sh
brew install python@3.11 python-tk@3.11 graphicsmagick

python3.11 -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements-dev.txt

# Confirm this Python has Tk support:
python -c "import tkinter; print(tkinter.TkVersion)"

# Point simulated drive roots anywhere writable:
export PHOTOLOG_N=$HOME/photolog-sim/N
export PHOTOLOG_H=$HOME/photolog-sim/H
export PHOTOLOG_GM=$(which gm)
mkdir -p "$PHOTOLOG_N" "$PHOTOLOG_H/hr" "$PHOTOLOG_H/TN" "$PHOTOLOG_N/TN"

python -m photolog
```

If you build the venv with a different Homebrew Python, install the matching Tk package first, for example `brew install python-tk@3.14` for Python 3.14.

On Windows these env vars are unused and real `N:\RPMS$` / `H:\PLGwww` paths apply.

## Manifest / resume

- Copy manifest lives at `<dest>\.photolog-copy-manifest.json`. Partial writes leave `<name>.photolog-partial` files — rerunning the app auto-resumes.
- Thumbnail manifest at `<tn-dest>\.photolog-thumb-manifest.json` records successes/failures; "skip existing" defaults ON and keys on size + mtime.

## Junctions

Created with `cmd /c mklink /J` on Windows (no admin required) and emulated with symlinks in the dev loop. If a link already exists pointing at a different target, the job aborts and surfaces the conflict rather than silently re-pointing.
