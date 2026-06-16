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

python -m photolog --dev
```

Then in the app, open the **Dev** tab and click **"Use sim tree paths"** (auto-creates the sim directory structure and writes to your config). Click **"Seed"** to populate fake segments.

**Optional:** If you need to override paths for testing, set env vars (rarely needed once config is set):
```sh
export PHOTOLOG_HR=$HOME/my-test-hr
export PHOTOLOG_GM=$(which gm)
python -m photolog --dev
```

If you build the venv with a different Homebrew Python, install the matching Tk package first (e.g., `brew install python-tk@3.14` for Python 3.14).

## Manifest / resume

- Copy manifest lives at `<dest>\.photolog-copy-manifest.json`. Partial writes leave `<name>.photolog-partial` files — rerunning the app auto-resumes.
- Thumbnail manifest at `<tn-dest>\.photolog-thumb-manifest.json` records successes/failures; "skip existing" defaults ON and keys on size + mtime.

## Junctions

Created with `cmd /c mklink /J` on Windows (no admin required) and emulated with symlinks in the dev loop. If a link already exists pointing at a different target, the job aborts and surfaces the conflict rather than silently re-pointing.

## Crash recovery & unattended operation

Photolog is designed to survive unattended operation on Windows Server with graceful recovery from crashes and failures:

**Startup safety:**
- Orphaned manifest temp files (`*.tmp`) from prior crashes are cleaned up automatically at launch.
- Partial sidecars (`*.photolog-partial`) are cleaned as part of job resume for the specific files being copied; the app does not recursively sweep every year folder on launch.
- Corrupted manifests are rotated to `.json.corrupt` and the job restarts fresh.

**During operation:**
- Disk space is checked before copy starts (requires 1.5× source size free) and every 10 files during copy.
- Insufficient space halts the job cleanly, leaving manifests consistent for retry.
- Manifest writes are retried up to 3 times on Windows lock failures (antivirus scanners, etc.).
- Worker threads are non-daemon; on shutdown, the app waits up to 30 seconds for manifests to flush before exiting.

**Resume behavior:**
- Partial writes (`*.photolog-partial` sidecars) signal an incomplete file.
- Manifest entries track state: `pending`, `partial`, `done`.
- Rerunning the app resumes exactly where it left off; identical files (by size + mtime) are skipped.
- If a destination file exists but doesn't match the source, SHA-256 is compared to avoid wasteful recopies.

**Error visibility:**
- Manifest write failures emit error events and halt the job (previously silent).
- Partial file cleanup failures are logged with context so ops can investigate.
- Worker threads still alive after 30s shutdown are reported to stderr.

Because there is no network at runtime and all state is persisted locally, the app can safely recover from power loss, USB disconnection, or OS crash.
