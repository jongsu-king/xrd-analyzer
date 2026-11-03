"""Right-hand parameter panel: widgets ↔ AnalysisRecipe (manual peak edits are preserved)."""

from __future__ import annotations

import math
from collections.abc import Callable

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..models.recipe import AnalysisRecipe
from ..models.units import CU_KA1_A, CU_KA2_A, CU_KA_WEIGHTED_A
from .i18n import Translator


def dspin(
    lo: float,
    hi: float,
    dec: int,
    val: float,
    step: float | None = None,
    special: str | None = None,
) -> QDoubleSpinBox:
    w = QDoubleSpinBox()
    w.setRange(lo, hi)
    w.setDecimals(dec)
    w.setSingleStep(step if step is not None else 10 ** -max(dec - 1, 0))
    if special is not None:
        w.setSpecialValueText(special)
    w.setValue(val)
    return w


def ispin(lo: int, hi: int, val: int) -> QSpinBox:
    w = QSpinBox()
    w.setRange(lo, hi)
    w.setValue(val)
    return w


def combo(items: list[tuple[str, object]]) -> QComboBox:
    c = QComboBox()
    for label, data in items:
        c.addItem(label, data)
    return c


def _set(c: QComboBox, data: object) -> None:
    i = c.findData(data)
    c.setCurrentIndex(max(i, 0))


class ParamPanel(QWidget):
    def __init__(
        self, tr: Translator, on_apply: Callable[[], None], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.tr = tr
        self._labels: list[tuple[QLabel, str]] = []
        self.tabs = QTabWidget()
        lay = QVBoxLayout(self)
        lay.addWidget(self.tabs)
        self.apply_btn = QPushButton()
        self.apply_btn.clicked.connect(on_apply)
        lay.addWidget(self.apply_btn)
        self._build_pre()
        self._build_peaks()
        self._build_fit()
        self._build_size()
        self._build_ref()
        self.retranslate()

    def _row(self, form: QFormLayout, key: str, w: QWidget) -> None:
        lbl = QLabel()
        self._labels.append((lbl, key))
        form.addRow(lbl, w)

    def _page(self) -> QFormLayout:
        page = QWidget()
        form = QFormLayout(page)
        self.tabs.addTab(page, "")
        return form

    def _build_pre(self) -> None:
        f = self._page()
        self.wl_preset = combo(
            [
                ("—", None),
                (f"Cu Kα1 ({CU_KA1_A} Å)", CU_KA1_A),
                (f"Cu Kα avg ({CU_KA_WEIGHTED_A:.6f} Å)", CU_KA_WEIGHTED_A),
                ("custom", "custom"),
            ]
        )
        self.wl_value = dspin(0.05, 5.0, 7, CU_KA1_A, 0.0001)
        self.wl_preset.currentIndexChanged.connect(self._wl_changed)
        self.ka2 = combo(
            [("none", "none"), ("Rachinger strip", "strip"), ("fit Kα1/Kα2 doublet", "fit_doublet")]
        )
        self.lambda2 = dspin(0.05, 5.0, 7, CU_KA2_A, 0.0001)
        self.ratio = dspin(0.01, 0.99, 3, 0.5, 0.01)
        self.dup = combo([("error", "error"), ("average", "average"), ("keep first", "keep_first")])
        self.crop_min = dspin(0.0, 180.0, 3, 0.0, 0.5, special="—")
        self.crop_max = dspin(0.0, 180.0, 3, 0.0, 0.5, special="—")
        self.smooth = QCheckBox()
        self.sg_window = ispin(3, 501, 11)
        self.sg_window.setSingleStep(2)
        self.sg_order = ispin(0, 10, 3)
        self.baseline = combo(
            [
                ("SNIP", "snip"),
                ("ALS", "als"),
                ("iterative polynomial", "poly"),
                ("peak-masked Whittaker", "masked"),
                ("none", "none"),
            ]
        )
        self.snip = ispin(1, 5000, 40)
        self.als_lam = dspin(1.0, 12.0, 2, 6.0, 0.5)
        self.als_p = dspin(0.0001, 0.5, 4, 0.01, 0.001)
        self.poly = ispin(0, 12, 4)
        self.bg_scale = dspin(0.0, 100.0, 4, 1.0, 0.05)
        for key, w in [
            ("lbl.preset", self.wl_preset),
            ("lbl.wavelength", self.wl_value),
            ("lbl.ka2", self.ka2),
            ("lbl.lambda2", self.lambda2),
            ("lbl.ratio", self.ratio),
            ("lbl.dup", self.dup),
            ("lbl.crop_min", self.crop_min),
            ("lbl.crop_max", self.crop_max),
            ("lbl.smooth", self.smooth),
            ("lbl.sg_window", self.sg_window),
            ("lbl.sg_order", self.sg_order),
            ("lbl.baseline", self.baseline),
            ("lbl.snip", self.snip),
            ("lbl.als_lam", self.als_lam),
            ("lbl.als_p", self.als_p),
            ("lbl.poly", self.poly),
            ("lbl.bg_scale", self.bg_scale),
        ]:
            self._row(f, key, w)

    def _build_peaks(self) -> None:
        f = self._page()
        self.auto = QCheckBox()
        self.auto.setChecked(True)
        self.prom = dspin(1.0, 1000.0, 1, 6.0, 0.5)
        self.min_dist = dspin(0.0, 20.0, 3, 0.15, 0.01)
        self.min_w = dspin(0.0, 20.0, 3, 0.0, 0.01)
        self.max_w = dspin(0.0, 50.0, 2, 0.0, 0.1, special="—")
        self.max_peaks = ispin(1, 500, 40)
        self.edits = QLabel("—")
        self.edits.setWordWrap(True)
        for key, w in [
            ("lbl.auto", self.auto),
            ("lbl.prom", self.prom),
            ("lbl.min_dist", self.min_dist),
            ("lbl.min_w", self.min_w),
            ("lbl.max_w", self.max_w),
            ("lbl.max_peaks", self.max_peaks),
            ("lbl.edits", self.edits),
        ]:
            self._row(f, key, w)

    def _build_fit(self) -> None:
        f = self._page()
        self.profile = combo([("pseudo-Voigt", "pseudo_voigt"), ("Voigt", "voigt")])
        self.window = dspin(0.6, 20.0, 1, 4.0, 0.5)
        self.local_bg = combo([("linear", "linear"), ("constant", "constant"), ("none", "none")])
        for key, w in [
            ("lbl.profile", self.profile),
            ("lbl.window", self.window),
            ("lbl.local_bg", self.local_bg),
        ]:
            self._row(f, key, w)

    def _build_size(self) -> None:
        f = self._page()
        self.K = dspin(0.0, 2.0, 3, 0.0, 0.01, special="—")
        self.inst = combo(
            [
                ("not provided", "none"),
                ("constant FWHM", "constant"),
                ("Caglioti U,V,W", "caglioti"),
            ]
        )
        self.inst_fwhm = dspin(0.0, 5.0, 4, 0.05, 0.005)
        self.U = dspin(-1.0, 1.0, 6, 0.0, 0.0005)
        self.V = dspin(-1.0, 1.0, 6, 0.0, 0.0005)
        self.W = dspin(-1.0, 1.0, 6, 0.0, 0.0005)
        self.corr = combo(
            [("quadratic (Gaussian)", "quadratic"), ("linear (Lorentzian)", "linear")]
        )
        self.wh_min = ispin(3, 50, 3)
        for key, w in [
            ("lbl.K", self.K),
            ("lbl.inst", self.inst),
            ("lbl.inst_fwhm", self.inst_fwhm),
            ("lbl.U", self.U),
            ("lbl.V", self.V),
            ("lbl.W", self.W),
            ("lbl.corr", self.corr),
            ("lbl.wh_min", self.wh_min),
        ]:
            self._row(f, key, w)

    def _build_ref(self) -> None:
        f = self._page()
        self.ref_list = QListWidget()
        self.add_cubic_btn = QPushButton()
        self.load_ref_btn = QPushButton()
        self.remove_ref_btn = QPushButton()
        self.tol = dspin(0.001, 2.0, 3, 0.10, 0.01)
        self.min_ri = dspin(0.0, 100.0, 1, 5.0, 1.0)
        f.addRow(self.ref_list)
        f.addRow(self.add_cubic_btn)
        f.addRow(self.load_ref_btn)
        f.addRow(self.remove_ref_btn)
        self._row(f, "lbl.tol", self.tol)
        self._row(f, "lbl.min_ri", self.min_ri)

    def _wl_changed(self) -> None:
        data = self.wl_preset.currentData()
        self.wl_value.setEnabled(data == "custom")
        if isinstance(data, float):
            self.wl_value.setValue(data)

    def retranslate(self) -> None:
        t = self.tr
        for i, key in enumerate(["tab.pre", "tab.peaks", "tab.fit", "tab.size", "tab.ref"]):
            self.tabs.setTabText(i, t(key))
        for lbl, key in self._labels:
            lbl.setText(t(key))
        self.wl_preset.setItemText(0, t("msg.select_wl"))
        self.wl_preset.setItemText(3, t("msg.custom"))
        self.apply_btn.setText(t("btn.apply"))
        self.add_cubic_btn.setText(t("btn.add_cubic"))
        self.load_ref_btn.setText(t("btn.load_ref"))
        self.remove_ref_btn.setText(t("btn.remove_ref"))

    # ------------------------------------------------------------------ recipe <-> widgets
    def set_recipe(self, r: AnalysisRecipe) -> None:
        p, k, f, s, rf = r.preprocess, r.peaks, r.fit, r.size, r.reference
        if p.wavelength_A is None:
            self.wl_preset.setCurrentIndex(0)
        else:
            idx = next(
                (i for i in (1, 2) if abs(self.wl_preset.itemData(i) - p.wavelength_A) < 1e-9), 3
            )
            self.wl_preset.setCurrentIndex(idx)
            self.wl_value.setValue(p.wavelength_A)
        self._wl_changed()
        _set(self.ka2, p.kalpha2_mode)
        self.lambda2.setValue(p.wavelength2_A or CU_KA2_A)
        self.ratio.setValue(p.ka2_ratio)
        _set(self.dup, p.duplicate_policy)
        self.crop_min.setValue(p.crop_min_deg or 0.0)
        self.crop_max.setValue(p.crop_max_deg or 0.0)
        self.smooth.setChecked(p.smoothing_enabled)
        self.sg_window.setValue(p.smoothing_window)
        self.sg_order.setValue(p.smoothing_polyorder)
        _set(self.baseline, p.baseline_method)
        self.snip.setValue(p.snip_iterations)
        self.als_lam.setValue(math.log10(p.als_lambda))
        self.als_p.setValue(p.als_p)
        self.poly.setValue(p.poly_degree)
        self.bg_scale.setValue(p.background_scale)
        self.auto.setChecked(k.auto_detect)
        self.prom.setValue(k.prominence_sigma)
        self.min_dist.setValue(k.min_distance_deg)
        self.min_w.setValue(k.min_width_deg)
        self.max_w.setValue(k.max_width_deg or 0.0)
        self.max_peaks.setValue(k.max_peaks)
        adds = ", ".join(f"+{v:.3f}°" for v in k.manual_add_deg)
        rems = ", ".join(f"−{v:.3f}°" for v in k.manual_remove_deg)
        self.edits.setText(" ".join(x for x in (adds, rems) if x) or "—")
        _set(self.profile, f.profile)
        self.window.setValue(f.window_fwhm_factor)
        _set(self.local_bg, f.local_background)
        self.K.setValue(s.scherrer_K or 0.0)
        _set(self.inst, s.instrument_mode)
        self.inst_fwhm.setValue(
            s.instrument_fwhm_deg if s.instrument_fwhm_deg is not None else 0.05
        )
        self.U.setValue(s.caglioti_U or 0.0)
        self.V.setValue(s.caglioti_V or 0.0)
        self.W.setValue(s.caglioti_W or 0.0)
        _set(self.corr, s.correction)
        self.wh_min.setValue(s.wh_min_peaks)
        self.tol.setValue(rf.tolerance_deg)
        self.min_ri.setValue(rf.min_rel_intensity)

    def recipe(self, base: AnalysisRecipe) -> AnalysisRecipe:
        r = base.copy()
        p, k, f, s, rf = r.preprocess, r.peaks, r.fit, r.size, r.reference
        preset = self.wl_preset.currentData()
        p.wavelength_A = (
            None
            if preset is None
            else (self.wl_value.value() if preset == "custom" else float(preset))
        )
        p.kalpha2_mode = self.ka2.currentData()
        p.wavelength2_A = self.lambda2.value() if p.kalpha2_mode != "none" else p.wavelength2_A
        p.ka2_ratio = self.ratio.value()
        p.duplicate_policy = self.dup.currentData()
        p.crop_min_deg = self.crop_min.value() or None
        p.crop_max_deg = self.crop_max.value() or None
        p.smoothing_enabled = self.smooth.isChecked()
        p.smoothing_window = self.sg_window.value()
        p.smoothing_polyorder = self.sg_order.value()
        p.baseline_method = self.baseline.currentData()
        p.snip_iterations = self.snip.value()
        p.als_lambda = 10 ** self.als_lam.value()
        p.als_p = self.als_p.value()
        p.poly_degree = self.poly.value()
        p.background_scale = self.bg_scale.value()
        k.auto_detect = self.auto.isChecked()
        k.prominence_sigma = self.prom.value()
        k.min_distance_deg = self.min_dist.value()
        k.min_width_deg = self.min_w.value()
        k.max_width_deg = self.max_w.value() or None
        k.max_peaks = self.max_peaks.value()
        f.profile = self.profile.currentData()
        f.window_fwhm_factor = self.window.value()
        f.local_background = self.local_bg.currentData()
        s.scherrer_K = self.K.value() or None
        s.instrument_mode = self.inst.currentData()
        s.instrument_fwhm_deg = self.inst_fwhm.value() if s.instrument_mode == "constant" else None
        cag = s.instrument_mode == "caglioti"
        s.caglioti_U = self.U.value() if cag else None
        s.caglioti_V = self.V.value() if cag else None
        s.caglioti_W = self.W.value() if cag else None
        s.correction = self.corr.currentData()
        s.wh_min_peaks = self.wh_min.value()
        rf.tolerance_deg = self.tol.value()
        rf.min_rel_intensity = self.min_ri.value()
        return r
