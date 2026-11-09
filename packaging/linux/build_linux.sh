#!/usr/bin/env bash
# Build the Linux one-folder app, a portable .tar.gz and a Debian/Ubuntu .deb package.
# Usage: packaging/linux/build_linux.sh   (run from the repository root, venv active)
set -euo pipefail
VERSION=$(python -c "import xrd_analyzer; print(xrd_analyzer.__version__)")
pyinstaller --noconfirm --distpath dist --workpath build packaging/xrd-analyzer.spec
test -x dist/XRD-Analyzer/XRD-Analyzer
# portable archive: the program folder plus install.sh / uninstall.sh for a per-user
# install (~/.local/share/xrd-analyzer, ~/.local/bin/xrd-analyzer, menu entry)
STAGE=$(mktemp -d)/XRD-Analyzer
cp -R dist/XRD-Analyzer "$STAGE"
for f in install.sh uninstall.sh; do
  sed -e "s|@ID@|xrd-analyzer|g" -e "s|@EXE@|XRD-Analyzer|g" "packaging/linux/$f" > "$STAGE/$f"
  chmod +x "$STAGE/$f"
done
sed -e "s|^Exec=.*|Exec=@EXEC@ %F|" packaging/linux/xrd-analyzer.desktop > "$STAGE/xrd-analyzer.desktop"
cp packaging/icons/xrd-analyzer-256.png "$STAGE/xrd-analyzer.png"
tar -C "$(dirname "$STAGE")" -czf "dist/XRD-Analyzer-${VERSION}-linux-x86_64.tar.gz" XRD-Analyzer
# .deb
PKG=$(mktemp -d)/xrd-analyzer_${VERSION}_amd64
mkdir -p "$PKG/DEBIAN" "$PKG/opt" "$PKG/usr/bin" "$PKG/usr/share/applications" \
         "$PKG/usr/share/icons/hicolor/256x256/apps" "$PKG/usr/share/doc/xrd-analyzer"
cp -R dist/XRD-Analyzer "$PKG/opt/xrd-analyzer"
ln -s /opt/xrd-analyzer/xrd-analyzer-cli "$PKG/usr/bin/xrd-analyzer"
cp packaging/linux/xrd-analyzer.desktop "$PKG/usr/share/applications/"
cp packaging/icons/xrd-analyzer-256.png "$PKG/usr/share/icons/hicolor/256x256/apps/xrd-analyzer.png"
cp README.md LICENSE "$PKG/usr/share/doc/xrd-analyzer/"
SIZE=$(du -sk "$PKG" | cut -f1)
cat > "$PKG/DEBIAN/control" <<CONTROL
Package: xrd-analyzer
Version: ${VERSION}
Section: science
Priority: optional
Architecture: amd64
Installed-Size: ${SIZE}
Depends: libegl1, libgl1, libxkbcommon0, libxkbcommon-x11-0, libfontconfig1, libdbus-1-3, libxcb-cursor0, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, libxcb-randr0, libxcb-render-util0, libxcb-shape0, libxcb-xinerama0
Maintainer: xrd-analyzer contributors <noreply@users.noreply.github.com>
Homepage: https://github.com/jongsu-king/xrd-analyzer
Description: Analyzer for 1D theta-2theta X-ray diffraction patterns
 Desktop (PySide6) and command-line analyzer: baselines, Kα2 handling, peak
 detection and pseudo-Voigt/Voigt fitting with uncertainties, Bragg d-spacing,
 guarded Scherrer and Williamson-Hall analysis, CSV/JSON/PNG/SVG/HTML/PDF reports.
CONTROL
dpkg-deb --build --root-owner-group "$PKG" "dist/xrd-analyzer_${VERSION}_amd64.deb"
( cd dist && sha256sum "XRD-Analyzer-${VERSION}-linux-x86_64.tar.gz" "xrd-analyzer_${VERSION}_amd64.deb" > linux.sha256 )
ls -la dist
