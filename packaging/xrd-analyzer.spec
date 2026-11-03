# PyInstaller spec — builds a one-folder application with two executables that share one
# runtime:  "XRD-Analyzer" (windowed GUI) and "xrd-analyzer-cli" (console; no arguments
# also opens the GUI). On macOS the result is wrapped as "XRD Analyzer.app".
#   pyinstaller --noconfirm packaging/xrd-analyzer.spec
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parent  # noqa: F821  (defined by PyInstaller)
SRC = ROOT / "src"
VERSION = re.search(r'__version__ = "([^"]+)"', (SRC / "xrd_analyzer" / "__init__.py").read_text(encoding="utf-8")).group(1)
ICONS = ROOT / "packaging" / "icons"
ICON = str(ICONS / ("xrd-analyzer.icns" if sys.platform == "darwin" else "xrd-analyzer.ico"))

a = Analysis(
    [str(SRC / "xrd_analyzer" / "__main__.py")],
    pathex=[str(SRC)],
    hiddenimports=collect_submodules("xrd_analyzer") + [
        "matplotlib.backends.backend_qtagg",
        "matplotlib.backends.backend_agg",
        "matplotlib.backends.backend_pdf",
        "matplotlib.backends.backend_svg",
    ],
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide2", "IPython", "pytest", "pytestqt", "mypy"],
    noarchive=False,
)
pyz = PYZ(a.pure)
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="XRD-Analyzer", console=False,
          icon=ICON, upx=False)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="xrd-analyzer-cli", console=True,
          icon=ICON, upx=False)
coll = COLLECT(gui, cli, a.binaries, a.datas, name="XRD-Analyzer", upx=False)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="XRD Analyzer.app",
        icon=ICON,
        bundle_identifier="io.github.jongsu-king.xrd-analyzer",
        version=VERSION,
        info_plist={
            "CFBundleDisplayName": "XRD Analyzer",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "12.0",
            "NSHumanReadableCopyright": "MIT License",
        },
    )
