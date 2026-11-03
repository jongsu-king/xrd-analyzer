# Building executables and installers

All builds use [PyInstaller](https://pyinstaller.org) with `packaging/xrd-analyzer.spec`.
It produces a one-folder application with two executables that share one runtime:

| Executable | Purpose |
|---|---|
| `XRD-Analyzer` (`.exe` on Windows) | windowed GUI application |
| `xrd-analyzer-cli` (`.exe` on Windows) | console program: every CLI command; without arguments it also opens the GUI |

On macOS the folder is wrapped as `XRD Analyzer.app`. PyInstaller cannot cross-compile, so
each installer must be built **on its own operating system**. The release workflow
(`.github/workflows/release.yml`) does this on GitHub-hosted runners.

## Prerequisites (all platforms)
```bash
python3 -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev,build]"                          # PySide6, PyInstaller, Pillow, test tools
QT_QPA_PLATFORM=offscreen pytest                       # make sure the source tree is green
```
Icons are generated from code (`python packaging/make_icons.py`). The generated files are
committed in `packaging/icons/`.

## macOS (DMG)
```bash
packaging/macos/build_dmg.sh
```
Output: `dist/XRD Analyzer.app`, `dist/XRD-Analyzer-<version>-macos-<arch>.dmg` and a
`.sha256` file. The DMG contains the app, an `Applications` shortcut, the README and the
LICENSE. The build is ad-hoc signed only. To distribute without Gatekeeper warnings, sign
and notarise with your own Apple Developer ID:
```bash
codesign --deep --force --options runtime --sign "Developer ID Application: <you>" "dist/XRD Analyzer.app"
xcrun notarytool submit dist/XRD-Analyzer-<version>-macos-arm64.dmg --keychain-profile <profile> --wait
xcrun stapler staple dist/XRD-Analyzer-<version>-macos-arm64.dmg
```
The architecture follows the build machine (`arm64` on Apple Silicon). For an Intel build,
run the script on an Intel Mac.

## Windows (setup.exe + portable zip)
```powershell
pyinstaller --noconfirm --distpath dist --workpath build packaging\xrd-analyzer.spec
choco install innosetup -y        # or install Inno Setup 6 from jrsoftware.org
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /DAppVersion=0.1.0 packaging\windows\xrd-analyzer.iss
Compress-Archive -Path dist\XRD-Analyzer -DestinationPath dist\XRD-Analyzer-0.1.0-windows-x64-portable.zip
```
The installer (`dist\XRD-Analyzer-<version>-windows-x64-setup.exe`) does the following:
- installs per user by default (no admin rights); *Install for all users* is offered on request;
- adds a Start-menu entry, plus an optional desktop icon and an optional `PATH` entry for
  `xrd-analyzer-cli.exe`;
- registers an uninstaller;
- shows its pages in English, plus Korean if the Inno Setup installation provides
  `Korean.isl`.

Silent install for deployment: `XRD-Analyzer-<version>-windows-x64-setup.exe /VERYSILENT
/CURRENTUSER /DIR="C:\Tools\XRD Analyzer"`. To avoid SmartScreen warnings, sign the setup with
`signtool sign /fd sha256 /tr <timestamp-url> /td sha256 /a <file>`.

## Linux (.deb + .tar.gz)
```bash
sudo apt-get install -y libegl1 libgl1 libxkbcommon0 libxkbcommon-x11-0 libfontconfig1 libdbus-1-3 libxcb-cursor0
packaging/linux/build_linux.sh
```
Outputs:
- `dist/XRD-Analyzer-<version>-linux-x86_64.tar.gz`: portable folder.
- `dist/xrd-analyzer_<version>_amd64.deb`: installs to `/opt/xrd-analyzer`, adds
  `/usr/bin/xrd-analyzer`, a desktop entry and an icon, and declares the Qt runtime
  dependencies.

Build on the oldest distribution you want to support. glibc is forward-compatible only, and
the release uses Ubuntu 22.04 (glibc 2.35).

## Verify every build
Run these against the **frozen** build, not the source tree:
```bash
<app>/xrd-analyzer-cli selftest --out selftest.json      # analysis + all export formats
<app>/XRD-Analyzer gui-smoke --offscreen --out gui.json  # GUI opens, analyses, closes
```
Both exit with code 0 on success and write a JSON report (`"selftest": "PASS"`,
`"gui_smoke": "PASS"`). The release workflow runs these on every OS. It also installs the
Windows setup silently, runs the self-test from the installed copy and uninstalls it, and it
installs and removes the .deb. The JSON reports are published as
`build-verification-reports.zip` with each release.

## Making a release
1. Update `__version__` in `src/xrd_analyzer/__init__.py`, plus `CHANGELOG.md` and
   `docs/RELEASE_NOTES.md`.
2. Commit, then `git tag v<version> && git push origin v<version>`.
3. The release workflow builds and verifies all installers, then publishes the GitHub Release.
