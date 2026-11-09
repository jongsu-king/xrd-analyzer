## XRD Analyzer 0.1.2

Desktop and command-line analyzer for 1D θ–2θ X-ray diffraction patterns. **The analysis
is identical to 0.1.1**; this release adds automatic updates and more downloads.

### New
- **Automatic updates.** An installed copy checks GitHub once a day, downloads a newer
  release for your system, verifies it against `SHA256SUMS.txt` and installs it when you
  close the program (or at once with *Restart now*). *Help → Automatic updates* chooses
  between *download and install*, *only tell me* and *off*; *Help → Check for updates…* and
  `xrd-analyzer-cli update` check on demand. Source and pip installs are never modified.
- **Intel Macs**: `XRD-Analyzer-0.1.2-macos-x86_64.dmg`.
- The Linux archive includes `install.sh` / `uninstall.sh` (per-user, no root).
- One `SHA256SUMS.txt` for every file.

Users of 0.1.1 install 0.1.2 once by hand (0.1.1 has no updater); later releases arrive
automatically. `build-verification-reports.zip` holds the self-test and GUI smoke-test
reports that each build produced on its own operating system. Full guide:
[docs/INSTALL.md](https://github.com/jongsu-king/xrd-analyzer/blob/main/docs/INSTALL.md).
