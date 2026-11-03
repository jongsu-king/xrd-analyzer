"""Robustness regressions: header inference, malformed rows, non-finite inputs, non-converged
fits, variance propagation, cropping, provenance, batch, peak removal, batch statistics and
solver cancellation. Each test reproduces a concrete counter-example."""

import math

import numpy as np
import pytest

from xrd_analyzer.core import profiles
from xrd_analyzer.core.analysis import (
    AnalysisError,
    batch_peak_statistics,
    run_analysis,
    run_batch,
)
from xrd_analyzer.core.crystallography import (
    NOT_CALCULABLE,
    InstrumentBroadening,
    scherrer,
)
from xrd_analyzer.core.fit import AnalysisCancelled, fit_peaks
from xrd_analyzer.core.peaks import PeakCandidate
from xrd_analyzer.io import AxisConfirmationRequired, XRDFormatError, read_pattern
from xrd_analyzer.io.readers import classify_column
from xrd_analyzer.models.dataset import XRDPattern
from xrd_analyzer.models.recipe import AnalysisRecipe
from xrd_analyzer.models.units import CU_KA1_A

ROWS = "\n".join(f"{20 + 0.02 * i:.2f},{100 + i}" for i in range(50))


def _recipe(**pre):
    r = AnalysisRecipe()
    r.preprocess.wavelength_A = CU_KA1_A
    for k, v in pre.items():
        setattr(r.preprocess, k, v)
    return r


def _counts(x, peaks, bg=100.0, w=0.1, seed=0):
    y = np.full_like(x, bg)
    for c, h in peaks:
        y += profiles.pseudo_voigt(x, c, h, w, 0.5)
    return np.random.default_rng(seed).poisson(y).astype(float)


def test_uncertainty_columns_and_rates(write_text):
    assert classify_column("2theta_error_deg") == ("uncertainty", None)
    assert classify_column("esd") == ("uncertainty", None)
    assert classify_column("intensity_counts/s") == ("intensity", "cps")
    assert classify_column("counts per second") == ("intensity", "cps")
    assert classify_column("intensity_uncorrected")[0] != "uncertainty"
    with pytest.raises(AxisConfirmationRequired):
        read_pattern(write_text("e.csv", "2theta_error_deg,intensity_counts\n" + ROWS))
    pat = read_pattern(write_text("r.csv", "2theta_deg,intensity_counts/s\n" + ROWS))
    assert pat.intensity_unit == "cps" and not pat.counts_like


def test_malformed_first_row_is_reported(write_text):
    text = "two_theta_deg,intensity_counts\n10,BAD\n" + "\n".join(f"{v},{v}" for v in range(11, 16))
    with pytest.raises(XRDFormatError, match="line 2: malformed data row"):
        read_pattern(write_text("m.csv", text))


def test_key_value_preamble_still_allowed(write_text):
    text = "Sample,Si\nWavelength,1.5406\n2theta_deg,counts\n" + ROWS
    pat = read_pattern(write_text("p.csv", text))
    assert pat.n_points == 50 and pat.metadata["Wavelength"] == "1.5406"
    assert pat.metadata["x_column"] == "2theta_deg"


def test_nonfinite_or_mistyped_recipe_values_rejected(examples):
    ex = examples["nano_fcc"]
    r = ex.recipe.copy()
    r.size.instrument_fwhm_deg = float("nan")
    assert any("size.instrument_fwhm_deg" in e for e in r.validate())
    with pytest.raises(AnalysisError, match="finite"):
        run_analysis(ex.pattern, r)
    r2 = ex.recipe.copy()
    r2.peaks.prominence_sigma = "6"  # type: ignore[assignment]
    assert any("prominence_sigma" in e for e in r2.validate())
    r3 = ex.recipe.copy()
    r3.peaks.manual_add_deg = [float("inf")]
    assert any("manual_add_deg" in e for e in r3.validate())
    res = scherrer(40.0, 0.5, 0.01, 0.9, CU_KA1_A, InstrumentBroadening("constant", math.nan))
    assert res.status == NOT_CALCULABLE and res.value is None
    assert (
        scherrer(40.0, 0.5, 0.01, math.nan, CU_KA1_A, InstrumentBroadening("constant", 0.05)).status
        == NOT_CALCULABLE
    )


def test_nonconverged_fits_excluded(examples):
    ex = examples["nano_fcc"]
    r = ex.recipe.copy()
    r.fit.max_nfev = 1
    res = run_analysis(ex.pattern, r)
    flagged = [p for p in res.peaks if "not_converged" in p.fit["flags"]]
    assert flagged, "max_nfev=1 must leave non-converged peaks"
    assert all(p.scherrer.status == NOT_CALCULABLE for p in flagged)
    assert all(p.d_spacing.status == NOT_CALCULABLE for p in flagged)
    assert res.williamson_hall.status == NOT_CALCULABLE
    assert any("excluded" in w for w in res.warnings)


def test_duplicate_average_variance():
    x = np.repeat(np.arange(20.0, 30.0, 0.05), 4)
    pat = XRDPattern("dup", x, np.full(x.size, 100.0), "counts")
    res = run_analysis(pat, _recipe(duplicate_policy="average", baseline_method="none"))
    assert np.allclose(res.processed.sigma, 5.0)  # σ of the mean of 4 Poisson(100)


def test_background_interpolation_variance():
    xb = np.arange(20.0, 30.001, 0.1)
    xs = np.round(np.arange(20.05, 29.96, 0.1), 6)  # halfway between background samples
    pat = XRDPattern("s", xs, np.full(xs.size, 100.0), "counts")
    bg = XRDPattern("b", xb, np.full(xb.size, 100.0), "counts")
    res = run_analysis(pat, _recipe(baseline_method="none"), background=bg)
    assert np.allclose(res.processed.sigma**2, 150.0)  # 100 + 0.25·100 + 0.25·100


def test_background_unit_mismatch_and_strip_qualified(examples):
    ex = examples["si_like"]
    bg = XRDPattern("b", ex.pattern.two_theta_deg, np.ones(ex.pattern.n_points), "cps")
    with pytest.raises(AnalysisError, match="unit"):
        run_analysis(ex.pattern, ex.recipe, background=bg)
    r = examples["si_like_ka2"].recipe.copy()
    r.preprocess.kalpha2_mode = "strip"
    res = run_analysis(examples["si_like_ka2"].pattern, r)
    assert res.processed.sigma is None
    assert any("correlates the noise" in w for w in res.warnings)


def test_rejection_uses_post_crop_local_step():
    x = np.unique(np.r_[np.arange(1.0, 180.0, 1.0), np.round(np.linspace(30, 31, 101), 6)])
    y = _counts(x, [(30.5, 3000.0)], w=0.1)
    pat = XRDPattern("crop", x, y, "counts")
    res = run_analysis(pat, _recipe(crop_min_deg=30.0, crop_max_deg=31.0, baseline_method="none"))
    centres = [p.fit["center_deg"] for p in res.peaks]
    assert len(centres) == 1 and centres[0] == pytest.approx(30.5, abs=0.005)
    assert res.processed.axis_report.median_step_deg == pytest.approx(0.01)


def test_background_in_provenance(examples):
    ex = examples["si_like"]
    bg = XRDPattern(
        "holder", ex.pattern.two_theta_deg, np.full(ex.pattern.n_points, 20.0), "counts"
    )
    d = run_analysis(ex.pattern, ex.recipe, background=bg).to_dict()
    mb = d["provenance"]["measured_background"]
    assert mb["sha256"] == bg.sha256 and mb["scale"] == 1.0 and mb["intensity_unit"] == "counts"
    assert (
        run_analysis(ex.pattern, ex.recipe).to_dict()["provenance"]["measured_background"] is None
    )


def test_batch_applies_per_dataset_background():
    x = np.round(np.arange(25.0, 40.0, 0.01), 6)
    holder = _counts(x, [(30.0, 2000.0)], bg=50.0, seed=1)
    sample = _counts(x, [(30.0, 2000.0), (35.0, 2000.0)], bg=100.0, seed=2)
    pat = XRDPattern("sample", x, sample, "counts")
    bg = XRDPattern("holder", x, holder, "counts")
    r = _recipe()
    single = [round(p.fit["center_deg"], 1) for p in run_analysis(pat, r, bg).peaks]
    batch = run_batch([pat], r, backgrounds=[bg])[0].result
    assert [round(p.fit["center_deg"], 1) for p in batch.peaks] == single == [35.0]
    with pytest.raises(AnalysisError):
        run_batch([pat, pat], r, backgrounds=[bg])


def test_removal_uses_selected_peak_identity():
    x = np.round(np.arange(28.0, 33.0, 0.005), 6)
    pat = XRDPattern("close", x, _counts(x, [(30.0, 3000.0), (30.4, 3000.0)], w=0.15), "counts")
    res = run_analysis(pat, _recipe())
    second = max(res.peaks, key=lambda p: p.fit["center_deg"])
    assert res.removal_position(second.peak_id) == pytest.approx(30.4, abs=0.02)
    r = _recipe()
    r.peaks.manual_remove_deg = [res.removal_position(second.peak_id)]
    kept = [p.fit["center_deg"] for p in run_analysis(pat, r).peaks]
    assert len(kept) == 1 and kept[0] == pytest.approx(30.0, abs=0.01)


def test_batch_statistics_bounded_and_dataset_aware():
    x = np.round(np.arange(29.5, 30.7, 0.002), 6)
    rec = _recipe(baseline_method="none")
    rec.peaks.min_distance_deg = 0.03  # peaks are 0.09° apart
    one = XRDPattern(
        "one", x, _counts(x, [(30.0, 3e3), (30.09, 3e3), (30.18, 3e3)], w=0.02), "counts"
    )
    r1 = run_analysis(one, rec)
    assert len(r1.peaks) == 3
    rows = batch_peak_statistics([r1], tolerance_deg=0.1)
    assert [g["n_patterns"] for g in rows] == [1, 1, 1]
    assert all(g["two_theta_spread_deg"] <= 0.1 for g in rows)
    two = XRDPattern("two", x, _counts(x, [(30.01, 3e3)], w=0.02, seed=5), "counts")
    r2 = run_analysis(two, rec)
    rows2 = batch_peak_statistics([r1, r2], tolerance_deg=0.05)
    first = rows2[0]
    assert first["n_patterns"] == 2 and sorted(first["patterns"]) == ["one", "two"]


def test_cancel_inside_solver():
    x = np.round(np.arange(30, 40, 0.01), 6)
    y = profiles.pseudo_voigt(x, 35.0, 1000, 0.12, 0.4)
    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] > 3  # passes the group check, trips inside least_squares

    with pytest.raises(AnalysisCancelled):
        fit_peaks(x, y, [PeakCandidate(35.0, 1000, 0.12, 1000, "auto")], cancel=cancel)
    assert calls["n"] > 3
