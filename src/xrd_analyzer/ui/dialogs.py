"""Dialogs: column/axis confirmation on import, computed cubic reference, plot settings."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..io.readers import ProbeResult, ReadOptions
from ..reports.figures import PlotSettings


class ImportDialog(QDialog):
    """Ask the user to confirm columns, axis quantity (2θ vs θ) and units. Nothing is guessed:
    suggestions from the header are pre-filled but must be accepted explicitly."""

    def __init__(self, probe: ProbeResult, message: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Confirm data columns / 데이터 열 확인")
        lay = QVBoxLayout(self)
        info = QLabel(message)
        info.setWordWrap(True)
        lay.addWidget(info)
        table = QTableWidget(len(probe.preview), probe.n_columns)
        table.setHorizontalHeaderLabels(probe.header or [str(i) for i in range(probe.n_columns)])
        for r, row in enumerate(probe.preview):
            for c, v in enumerate(row):
                table.setItem(r, c, QTableWidgetItem(f"{v:g}"))
        table.setMaximumHeight(170)
        lay.addWidget(table)
        form = QFormLayout()
        self.x_col = QSpinBox()
        self.x_col.setRange(0, max(probe.n_columns - 1, 0))
        self.x_col.setValue(probe.x_column or 0)
        self.y_col = QSpinBox()
        self.y_col.setRange(0, max(probe.n_columns - 1, 0))
        self.y_col.setValue(probe.y_column if probe.y_column is not None else 1)
        self.quantity = QComboBox()
        self.quantity.addItem("2θ (two-theta)", "2theta")
        self.quantity.addItem("θ (theta) → 2θ = 2·θ", "theta")
        if probe.axis_quantity == "theta":
            self.quantity.setCurrentIndex(-1)  # ambiguous header: force an explicit choice
        self.unit = QComboBox()
        self.unit.addItem("degree", "deg")
        self.unit.addItem("radian", "rad")
        if probe.axis_unit == "rad":
            self.unit.setCurrentIndex(1)
        self.iunit = QComboBox()
        for u in ("counts", "cps", "a.u."):
            self.iunit.addItem(u, u)
        if probe.intensity_unit in ("cps", "a.u."):
            self.iunit.setCurrentIndex(("counts", "cps", "a.u.").index(probe.intensity_unit))
        self.drop = QCheckBox("drop rows with NaN/Inf (recorded as a quality flag)")
        form.addRow("2θ/θ column", self.x_col)
        form.addRow("intensity column", self.y_col)
        form.addRow("axis quantity", self.quantity)
        form.addRow("axis unit", self.unit)
        form.addRow("intensity unit", self.iunit)
        form.addRow(self.drop)
        lay.addLayout(form)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.error = QLabel("")
        self.error.setStyleSheet("color: #cf222e")
        lay.addWidget(self.error)

    def _accept(self) -> None:
        if self.quantity.currentIndex() < 0:
            self.error.setText("Choose whether the axis column is 2θ or θ.")
            return
        if self.x_col.value() == self.y_col.value():
            self.error.setText("2θ and intensity columns must differ.")
            return
        self.accept()

    def options(self) -> ReadOptions:
        return ReadOptions(
            x_column=self.x_col.value(),
            y_column=self.y_col.value(),
            axis_quantity=self.quantity.currentData(),
            axis_unit=self.unit.currentData(),
            intensity_unit=self.iunit.currentData(),
            drop_nonfinite=self.drop.isChecked(),
        )


class CubicReferenceDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Computed cubic reference (positions only)")
        form = QFormLayout(self)
        self.name = QLineEdit("cubic phase")
        self.a = QDoubleSpinBox()
        self.a.setDecimals(6)
        self.a.setRange(1.0, 50.0)
        self.a.setValue(5.431020511)
        self.centering = QComboBox()
        for c in ("P", "I", "F", "diamond"):
            self.centering.addItem(c, c)
        self.centering.setCurrentIndex(3)
        form.addRow("name", self.name)
        form.addRow("a (Å)", self.a)
        form.addRow("lattice", self.centering)
        form.addRow(QLabel("No intensities / structure factors are computed."))
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)


class PlotSettingsDialog(QDialog):
    def __init__(self, s: PlotSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Plot settings / 그래프 설정")
        form = QFormLayout(self)
        self.title = QLineEdit(s.title)
        self.xlabel = QLineEdit(s.xlabel)
        self.ylabel = QLineEdit(s.ylabel)
        self.font = QDoubleSpinBox()
        self.font.setRange(6, 30)
        self.font.setValue(s.font_size)
        self.checks: dict[str, QCheckBox] = {}
        form.addRow("title", self.title)
        form.addRow("x label", self.xlabel)
        form.addRow("y label", self.ylabel)
        form.addRow("font size", self.font)
        for key in (
            "show_legend",
            "show_raw",
            "show_baseline",
            "show_net",
            "show_fit",
            "show_components",
            "show_residual",
            "show_peak_markers",
            "log_y",
        ):
            cb = QCheckBox(key.replace("_", " "))
            cb.setChecked(bool(getattr(s, key)))
            self.checks[key] = cb
            form.addRow(cb)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)
        self._dark = s.dark

    def settings(self) -> PlotSettings:
        return PlotSettings(
            title=self.title.text(),
            xlabel=self.xlabel.text(),
            ylabel=self.ylabel.text(),
            font_size=self.font.value(),
            dark=self._dark,
            **{k: cb.isChecked() for k, cb in self.checks.items()},
        )
