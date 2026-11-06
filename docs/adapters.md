# Adding a native-format adapter (e.g. Rigaku `.ras` / `.rasx`)

Vendor formats are `UNSUPPORTED` until a parser is validated against **real files from the
instrument and software version in question**. Do not implement a format from memory or from third-party descriptions alone.

## Status of existing adapters
Bruker `.brml` (`io/bruker.py`) was implemented against four real files from one D2 PHASER and
DIFFRAC version 8.6.3.0. Each was compared with the same scan's vendor text export: same 2θ
grid, and counts ÷ time equal to the exported rates within rounding. Run the comparison with
`XRD_REAL_DATA_DIR=<folder> pytest tests/integration/test_real_bruker_files.py`.

Two prerequisites below are **not** met yet, so the reader refuses everything else: all four
files are the same scan mode (no multi-scan file, no absorber), and the real files are not
committed. The committed tests use synthetic archives with the same XML layout.

## Prerequisites
1. At least 3 real exports per format/software version (different scan modes, a
   multi-scan file, and an attenuator change if applicable), with written permission to
   commit them (or anonymised derivatives) as test fixtures. Never commit private research
   data.
2. The same scans exported as plain text by the vendor software. This is the reference used to
   check the adapter (same 2θ grid and intensities, after the documented unit conversions).
3. The software name/version that wrote each file (record it in the adapter's docstring and README).

## Implementation checklist
- New class in `io/readers.py` (or `io/rigaku.py`) implementing `supports / probe / parse /
  validate / metadata`; register it in `io/registry.py` **before** the `VendorFormatStub`.
- Parse explicitly: scan axis name (must be 2θ/θ–2θ — reject ω/χ/φ/RSM scans with a
  clear error until those modes are implemented), start/step/count, intensity unit
  (counts vs cps, counting time), attenuator/correction factors, Kα1/Kα2 wavelengths in
  the header (show to the user; do not silently override the recipe).
- Keep the raw file immutable; record the SHA-256, software version and every header key
  you used in `XRDPattern.metadata`.
- For `.rasx` (a zip container): check the archive safely (path traversal, size limits,
  a corrupted archive gives a clear error).
- Tests: golden comparison with the vendor text export (exact grid, intensities within
  rounding), header-unit tests, truncated/corrupted files, unknown version → explicit error.
- Only then change the status to `VERIFIED_NATIVE` for the tested software versions and
  update the README format table and `docs/scientific_validation/CLAIMS_MATRIX.md`.
