# Changelog

## Unreleased
Fixes from the first test on real instrument files (four scans, Bruker D2 PHASER).

**Results change for this release.** More weak peaks are found, and baselines sit higher.

- **Import dialog no longer defaults the intensity unit to counts.** Count rates accepted as
  shown lost most peaks without a warning (7 → 2 on a real scan). No unit is pre-selected
  unless the file points to one, and `counts` is unavailable for fractional values.
- Fractional values labelled as counts are refused. A reduced χ² below 0.5 is flagged.
- **New reader for Bruker `.brml`** (coupled 2θ/θ PSD fast scan, DIFFRAC 8.6.3.0): raw counts,
  columns taken from the file, wavelengths reported but never applied. Everything else is
  refused with a message. Bruker `.xy` headers are parsed into metadata, and `cps` is offered
  when the header's counting time proves it.
- **Peak detection judges the whole peak.** Candidates also come from Gaussian-smoothed data, and
  the fit confirms them by √Δχ² ≥ k (exported as `significance_sigma`) instead of by height in
  units of a single point's σ. Weak peaks spread over many points are now found, and results no
  longer depend on how finely a scan is sampled. Removed `doublet_observable_height`.
- SNIP, ALS and polynomial baselines no longer sit about 2σ under the mean background. SNIP
  treats points near the scan ends like interior ones.
- New flags: `shape_at_bound`, and `poor_profile_fit`, judged per peak so a wide window cannot
  hide it, with a test that also works for unweighted fits. The uncertainty model states that σ
  values are statistical only.
- A seed width far from the fitted width is redone, so noise in a weak peak cannot merge its
  fit window with a strong neighbour's.
- Doublet fits state that height and area are Kα1 only. The provenance `variance_model` now
  describes the model actually used. The file's wavelength is compared with the recipe's.
- CLI: `--prominence-sigma`, `--smooth`; `--wavelength cu_ka1` fills in Kα2 when needed.
- **Open results in OriginLab Origin** (*File → Open in Origin…*, `--origin`, `open-in-origin`): a
  folder with CSV, a LabTalk script and a launcher makes Origin build the workbooks and graphs and
  save an `.opju`. The Windows installer can register `.xrdorigin` so a double-click starts it.
  **Not yet tested with real Origin.**
- A candidate list cut at `max_peaks` now gives a warning.
- `tests/regression/golden_v0_1_0.json` regenerated: the baseline change moves the synthetic
  `nano_fcc` results by up to 0.001° in position and 0.003° in FWHM, both within the accuracy
  against the known answers (W–H size 14.96 → 14.99 nm, truth 15 nm).

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
