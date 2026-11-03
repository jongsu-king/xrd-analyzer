## XRD Analyzer 0.1.1

This is a desktop and command-line analyzer for 1D θ–2θ X-ray diffraction patterns.
Version 0.1.1 is the first release archived on Zenodo with a DOI. The analysis is identical to
0.1.0; this release fixes the Windows build and the macOS checksum file.

### Downloads
| System | File |
|---|---|
| Windows 10/11 x64 | `XRD-Analyzer-0.1.1-windows-x64-setup.exe` (installer) or `…-portable.zip` |
| macOS 12+ Apple Silicon | `XRD-Analyzer-0.1.1-macos-arm64.dmg` |
| Ubuntu/Debian x86_64 | `xrd-analyzer_0.1.1_amd64.deb` |
| Other Linux x86_64 | `XRD-Analyzer-0.1.1-linux-x86_64.tar.gz` |

The builds are **not code-signed**. See *First start on unsigned builds* in the README.
Checksums are in the `.sha256` files. `build-verification-reports.zip` holds the self-test
and GUI smoke-test reports that each installer produced on its own operating system during
the release build.

### Highlights
- Text import (CSV/TXT/XY/DAT/TSV) that never guesses the axis (2θ vs θ) or units.
- Baselines: SNIP, ALS, polynomial, and a peak-masked Whittaker baseline that separates
  amorphous humps.
- Kα2 stripping or Kα1/Kα2 doublet fitting. Pseudo-Voigt/Voigt fits with uncertainties and
  quality flags.
- Bragg d-spacings; Scherrer and Williamson–Hall only when their inputs are valid.
- CSV/JSON/PNG/SVG/HTML/PDF outputs, hash-verified projects, batch statistics.
- Korean/English interface, light/dark themes, undo/redo, background processing with
  cancel and pause.
- Built-in `selftest`: run `xrd-analyzer-cli selftest` after installing.

### Known limitations
Validated on synthetic ground-truth data only. Vendor binary formats (.ras/.rasx/.xrdml/.raw)
are not supported. Export text from the instrument software instead. There is no Intel macOS
build. See the README for the full list.
