"""Light (default) and dark Fusion palettes."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


def apply_theme(app: QApplication, dark: bool) -> None:
    app.setStyle("Fusion")
    if not dark:
        app.setPalette(app.style().standardPalette())
        return
    p = QPalette()
    roles = {
        QPalette.ColorRole.Window: "#1e1f22",
        QPalette.ColorRole.WindowText: "#e6edf3",
        QPalette.ColorRole.Base: "#161719",
        QPalette.ColorRole.AlternateBase: "#24262a",
        QPalette.ColorRole.ToolTipBase: "#2b2d31",
        QPalette.ColorRole.ToolTipText: "#e6edf3",
        QPalette.ColorRole.Text: "#e6edf3",
        QPalette.ColorRole.Button: "#2b2d31",
        QPalette.ColorRole.ButtonText: "#e6edf3",
        QPalette.ColorRole.Highlight: "#4493f8",
        QPalette.ColorRole.HighlightedText: "#ffffff",
        QPalette.ColorRole.Link: "#4493f8",
    }
    for role, color in roles.items():
        p.setColor(role, QColor(color))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#6e7681"))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#6e7681"))
    app.setPalette(p)
