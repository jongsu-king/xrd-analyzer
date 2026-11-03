"""Matplotlib canvases embedded in Qt (zoom/pan via the standard navigation toolbar)."""

from __future__ import annotations

from collections.abc import Callable

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from PySide6.QtWidgets import QVBoxLayout, QWidget


class PlotPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.figure = Figure(figsize=(8, 5), layout="constrained")
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.toolbar)
        lay.addWidget(self.canvas)
        self._click: Callable[[float], None] | None = None
        self.canvas.mpl_connect("button_press_event", self._on_click)

    def set_click_handler(self, fn: Callable[[float], None] | None) -> None:
        self._click = fn

    def _on_click(self, event: object) -> None:
        if self._click is None or getattr(event, "xdata", None) is None:
            return
        if self.toolbar.mode:  # zoom/pan active: do not add peaks
            return
        if getattr(event, "inaxes", None) is not self.figure.axes[0]:
            return
        self._click(float(event.xdata))  # type: ignore[attr-defined]

    def redraw(self) -> None:
        self.canvas.draw_idle()
