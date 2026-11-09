# Changelog

## 0.1.3 — 2026-11-09
- Automatic updates of the installed macOS and Linux builds: the updater now falls back to
  the system CA bundle, so it can verify GitHub's certificate (0.1.2 could not; install
  0.1.3 once by hand on those systems). No changes to the analysis.

## 0.1.2 — 2026-11-09
- **Automatic updates** for installed copies: a daily check of the latest GitHub release,
  download of the file for this system, SHA-256 verification against the release's
  `SHA256SUMS.txt`, installation on exit or on *Restart now*. *Help → Automatic updates*
  (download and install / only tell me / off), *Help → Check for updates…*, and
  `xrd-analyzer-cli update [--check-only]`. Source and pip installs are never modified.
- macOS disk image for **Intel** Macs (`…-macos-x86_64.dmg`) next to the Apple-silicon one.
- The Linux `.tar.gz` now contains `install.sh`/`uninstall.sh` for a per-user install.
- Releases carry one `SHA256SUMS.txt` instead of per-platform `.sha256` files, and the
  release notes list every file with its size and checksum.
- No changes to the analysis.

## 0.1.1 — 2026-11-03
- Archived on Zenodo with a DOI (metadata in `.zenodo.json` and `CITATION.cff`).
- Windows build: the version file is read as UTF-8.
- macOS DMG checksum file lists the bare file name, so `shasum -c` works after download.
- No changes to the analysis.

## 0.1.0 — 2026-11-03
First public release.

- Delimited-text import (CSV/TXT/XY/DAT/TSV) with explicit confirmation of the axis quantity
  (2θ vs θ), unit (deg/rad) and columns; vocabulary-based header recognition, uncertainty
  columns never auto-selected, malformed rows reported with line numbers.
- Pre-processing: duplicate-2θ policy, crop, measured-background subtraction with variance
  propagation, Kα2 stripping (Rachinger) or Kα1/Kα2 doublet fitting, Savitzky–Golay for
  detection, baselines SNIP / ALS / iterative polynomial / peak-masked Whittaker.
- Peaks: Poisson- or rolling-MAD-thresholded detection, manual add/remove stored in the
  recipe, post-fit confirmation; pseudo-Voigt/Voigt fitting with covariance-based
  uncertainties, residuals, χ²_red, convergence and identifiability flags.
- Crystallography: Bragg d-spacing with σ_d; Scherrer and Williamson–Hall only when their
  inputs are valid (otherwise `NOT_CALCULABLE` with the reason); position-only phase
  candidates against user-licensed or computed cubic references.
- Outputs: CSV, JSON (full provenance), PNG/SVG/PDF figures, HTML and PDF reports;
  project files (hash-verified, measured backgrounds embedded) and recipes; batch analysis
  with batch statistics.
- PySide6 GUI (background worker with progress/cancel/pause, undo/redo, Korean/English,
  light/dark), CLI, built-in `selftest` and `gui-smoke` commands.
- Installers: macOS DMG (arm64), Windows setup + portable zip (x64), Linux .deb + .tar.gz
  (x86_64); every build verifies itself in the release workflow.
- 294 automated tests, analysis-core coverage 95 %.
