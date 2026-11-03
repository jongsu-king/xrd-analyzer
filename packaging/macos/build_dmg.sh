#!/usr/bin/env bash
# Build "XRD Analyzer.app" and a compressed DMG (drag-to-Applications layout).
# Usage: packaging/macos/build_dmg.sh   (run from the repository root, venv active)
set -euo pipefail
VERSION=$(python -c "import xrd_analyzer; print(xrd_analyzer.__version__)")
ARCH=$(uname -m)
pyinstaller --noconfirm --distpath dist --workpath build packaging/xrd-analyzer.spec
APP="dist/XRD Analyzer.app"
test -d "$APP"
STAGE=$(mktemp -d)
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
cp README.md LICENSE "$STAGE/" 2>/dev/null || true
OUT="dist/XRD-Analyzer-${VERSION}-macos-${ARCH}.dmg"
rm -f "$OUT"
hdiutil create -volname "XRD Analyzer ${VERSION}" -srcfolder "$STAGE" -ov -format UDZO "$OUT"
rm -rf "$STAGE"
shasum -a 256 "$OUT" > "$OUT.sha256"
echo "$OUT"
