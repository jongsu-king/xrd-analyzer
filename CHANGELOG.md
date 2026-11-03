# Changelog

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
