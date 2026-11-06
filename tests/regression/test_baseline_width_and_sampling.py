"""Baselines must leave broad peaks alone, and settings must not depend on the sampling step.

An offset added to lower-envelope baselines once followed any peak wider than its window: at the
step of a real scan, a peak of FWHM 1° lost two thirds of its area with no warning, and Scherrer
sizes came out too large. Settings counted in points also took peaks into the baseline on finely
sampled scans, which then showed no peaks at all.
"""

import math

import numpy as np
import pytest

from xrd_analyzer.core import preprocessing as proc
from xrd_analyzer.core.analysis import AnalysisError, baseline_rise, run_analysis
from xrd_analyzer.core.peaks import detect_peaks
from xrd_analyzer.io import XRDFormatError, read_pattern
from xrd_analyzer.models.dataset import XRDPattern
from xrd_analyzer.models.recipe import AnalysisRecipe

AREA_PER_HEIGHT_FWHM = math.sqrt(math.pi / (4 * math.log(2)))  # of a Gaussian


def _peak(x, center, fwhm, height):
    return height * np.exp(-4 * math.log(2) * ((x - center) / fwhm) ** 2)


def _scan(step, fwhm, height=2000.0, level=200.0, seed=3, center=40.0):
    x = np.arange(20.0, 60.0, step)
    return x, np.random.default_rng(seed).poisson(level + _peak(x, center, fwhm, height)).astype(
        float
    )


def _main_peak(x, y, method, center=40.0, fwhm=1.0):
    recipe = AnalysisRecipe()
    recipe.preprocess.wavelength_A = 1.5406
    recipe.preprocess.baseline_method = method
    res = run_analysis(XRDPattern("p", x, y, "counts"), recipe)
    near = [p for p in res.fit.peaks if p.ok and abs(p.center_deg - center) < fwhm]
    assert near, f"no peak found; warnings: {res.warnings}"
    return max(near, key=lambda p: p.height), res


@pytest.mark.parametrize(
    ("step", "fwhm", "method", "least"),
    [
        (0.0101, 0.5, "snip", 0.95),
        (0.0101, 0.5, "poly", 0.97),
        (0.0101, 1.0, "snip", 0.87),
        (0.0101, 1.0, "poly", 0.97),
        (0.0101, 2.0, "poly", 0.95),
        (0.0200, 1.0, "snip", 0.96),
        (0.0200, 2.0, "poly", 0.96),
        (0.0101, 1.0, "masked", 0.98),
        (0.0101, 2.0, "masked", 0.98),
    ],
)
def test_broad_peaks_keep_their_area(step, fwhm, method, least):
    x, y = _scan(step, fwhm)
    p, _ = _main_peak(x, y, method, fwhm=fwhm)
    assert p.area / (2000.0 * fwhm * AREA_PER_HEIGHT_FWHM) > least
    assert p.fwhm_deg / fwhm > least


@pytest.mark.parametrize("step", [0.005, 0.002, 0.001, 0.0005, 0.0002])
@pytest.mark.parametrize("method", ["snip", "masked", "als", "poly"])
def test_results_do_not_depend_on_how_finely_the_scan_is_sampled(step, method):
    x, y = _scan(step, 0.15)
    p, res = _main_peak(x, y, method, fwhm=0.15)
    assert p.height / 2000.0 == pytest.approx(1.0, abs=0.02)
    assert p.area / (2000.0 * 0.15 * AREA_PER_HEIGHT_FWHM) == pytest.approx(1.0, abs=0.02)
    assert p.fwhm_deg / 0.15 == pytest.approx(1.0, abs=0.02)
    assert "baseline_absorbs_peak" not in p.flags
    assert not [w for w in res.warnings if "baseline" in w]


def test_a_weak_peak_is_found_on_a_finely_sampled_scan():
    # 2σ per point at this step, far under any single-point test, yet clear as a whole
    x = np.arange(20.0, 60.0, 0.0005)
    level = 200.0
    height = 2.0 * math.sqrt(level)
    y = np.random.default_rng(1).poisson(level + _peak(x, 40.0, 0.15, height)).astype(float)
    p, _ = _main_peak(x, y, "snip", fwhm=0.15)
    assert p.center_deg == pytest.approx(40.0, abs=0.02)
    assert p.height == pytest.approx(height, rel=0.25)


def test_noise_on_a_finely_sampled_scan_gives_no_peaks():
    x = np.arange(20.0, 40.0, 0.0005)
    for seed in range(3):
        y = np.random.default_rng(seed).poisson(100.0, x.size).astype(float)
        recipe = AnalysisRecipe()
        recipe.preprocess.wavelength_A = 1.5406
        assert run_analysis(XRDPattern("p", x, y, "counts"), recipe).fit.peaks == []


def test_a_coarse_or_ordinary_scan_uses_every_point_for_the_baseline():
    for step in (0.0101, 0.02, 0.05):
        x = np.arange(20.0, 60.0, step)
        assert proc.coarsening_factor(x) == 1
    assert proc.coarsening_factor(np.arange(20.0, 60.0, 0.005)) == 2
    assert proc.coarsening_factor(np.arange(20.0, 60.0, 0.001)) == 10


@pytest.mark.parametrize("method", ["snip", "masked", "als", "poly"])
def test_a_narrow_fine_scan_keeps_enough_points_for_its_baseline(method):
    x = 30.0 + 1e-5 * np.arange(800)
    assert proc.coarsening_factor(x) == 8
    y = np.random.default_rng(0).poisson(100 + _peak(x, 30.004, 0.001, 900.0)).astype(float)
    recipe = AnalysisRecipe()
    recipe.preprocess.wavelength_A = 1.5406
    recipe.preprocess.baseline_method = method
    assert run_analysis(XRDPattern("p", x, y, "counts"), recipe).processed.baseline.size == 800


def test_block_helpers_cover_every_point():
    v = np.arange(10.0)
    assert proc.block_means(v, 3).tolist() == [1.0, 4.0, 7.0, 9.0]
    assert proc.block_means(v, 1) is not None and proc.block_means(v, 1).size == 10
    mask = np.zeros(10, dtype=bool)
    mask[4] = True
    assert proc.block_any(mask, 3).tolist() == [False, True, False, False]
    assert proc.coarsening_factor(np.array([1.0])) == 1


def test_fine_scan_note_says_what_was_done():
    x, y = _scan(0.001, 0.15)
    _, res = _main_peak(x, y, "snip", fwhm=0.15)
    assert any("means of 10 points" in n for n in res.notes)


def test_a_dense_pattern_keeps_its_baseline_near_the_background():
    x = np.arange(20.0, 60.0, 0.0101)
    mean = 200 + sum(_peak(x, c, 0.3, 1500.0) for c in np.arange(21.0, 59.0, 1.0))
    y = np.random.default_rng(3).poisson(mean).astype(float)
    assert np.mean(proc.baseline_snip(y, 40)) - 200.0 < 4.0 * math.sqrt(200.0)


def test_a_manual_peak_on_a_fine_scan_is_masked_and_fitted_at_its_width():
    x = np.arange(20.0, 60.0, 0.001)
    y = np.random.default_rng(0).poisson(100 + _peak(x, 40.0, 0.15, 900.0)).astype(float)
    recipe = AnalysisRecipe()
    recipe.preprocess.wavelength_A = 1.5406
    recipe.preprocess.baseline_method = "masked"
    recipe.peaks.auto_detect = False
    recipe.peaks.manual_add_deg = [40.0]
    p = run_analysis(XRDPattern("p", x, y, "counts"), recipe).fit.peaks[0]
    assert p.height == pytest.approx(900.0, rel=0.05)
    assert p.fwhm_deg == pytest.approx(0.15, rel=0.05)


@pytest.mark.parametrize("method", ["snip", "masked"])
def test_a_scan_with_two_steps_keeps_the_peaks_of_both_parts(method):
    x = np.concatenate((np.arange(20.0, 30.0, 0.001), np.arange(30.0, 60.0, 0.05)))
    y = _peak(x, 25.0, 0.15, 900.0) + _peak(x, 45.0, 0.3, 900.0)
    y = np.random.default_rng(0).poisson(100 + y).astype(float)
    assert proc.coarsening_factor(x) == 1
    recipe = AnalysisRecipe()
    recipe.preprocess.wavelength_A = 1.5406
    recipe.preprocess.baseline_method = method
    peaks = run_analysis(XRDPattern("p", x, y, "counts"), recipe).fit.peaks
    found = {c: [p for p in peaks if abs(p.center_deg - c) < 0.1] for c in (25.0, 45.0)}
    assert found[25.0] and found[45.0]
    assert found[45.0][0].fwhm_deg == pytest.approx(0.3, rel=0.25)


# ------------------------------------------------------------------ the warning


def test_a_peak_the_baseline_follows_is_flagged():
    x, y = _scan(0.0101, 2.0)
    p, res = _main_peak(x, y, "snip", fwhm=2.0)
    assert "baseline_absorbs_peak" in p.flags
    message = next(w for w in res.warnings if "baseline follows" in w)
    assert f"{p.center_deg:.2f}°" in message and "masked" in message
    assert p.area / (2000.0 * 2.0 * AREA_PER_HEIGHT_FWHM) < 0.75  # the warning is deserved


@pytest.mark.parametrize("method", ["snip", "masked", "als", "poly"])
@pytest.mark.parametrize("fwhm", [0.08, 0.15, 0.25])
def test_an_ordinary_peak_is_not_flagged(method, fwhm):
    p, res = _main_peak(*_scan(0.0101, fwhm), method, fwhm=fwhm)
    assert "baseline_absorbs_peak" not in p.flags
    assert not [w for w in res.warnings if "baseline follows" in w]


def test_a_peak_on_a_sloping_background_is_not_flagged():
    x = np.arange(20.0, 60.0, 0.0101)
    mean = 100 + 40 * (x - 20) + 5000 * np.exp(-(x - 20) / 4) + _peak(x, 40.0, 0.2, 2000.0)
    y = np.random.default_rng(2).poisson(mean).astype(float)
    p, _ = _main_peak(x, y, "snip", fwhm=0.2)
    assert "baseline_absorbs_peak" not in p.flags


def test_a_peak_on_an_amorphous_hump_is_not_flagged():
    x = np.arange(20.0, 60.0, 0.0101)
    mean = 100 + _peak(x, 40.0, 12.0, 1500.0) + _peak(x, 38.0, 0.15, 1500.0)
    y = np.random.default_rng(2).poisson(mean).astype(float)
    p, _ = _main_peak(x, y, "masked", center=38.0, fwhm=0.15)
    assert p.fwhm_deg == pytest.approx(0.15, rel=0.1)
    assert "baseline_absorbs_peak" not in p.flags


def test_baseline_rise_is_measured_against_the_height_left():
    class Peak:
        center_deg, fwhm_deg, height = 40.0, 1.0, 100.0

    x = np.arange(20.0, 60.0, 0.01)
    assert baseline_rise(Peak, x, np.full(x.size, 50.0)) == pytest.approx(0.0)
    assert baseline_rise(Peak, x, 3.0 * x) == pytest.approx(0.0, abs=1e-9)
    assert baseline_rise(Peak, x, _peak(x, 40.0, 1.0, 10.0)) == pytest.approx(0.1, rel=0.01)
    Peak.height = 0.0
    assert baseline_rise(Peak, x, _peak(x, 40.0, 1.0, 10.0)) == 0.0


# ------------------------------------------------------------------ odd data


@pytest.mark.parametrize("index", [0, -1])
def test_a_peak_centre_stays_inside_the_scan(index):
    x = np.round(np.arange(20.0, 60.0, 0.02), 6)
    y = np.full(x.size, 10.0)
    y[index] = 1e6
    recipe = AnalysisRecipe()
    recipe.preprocess.wavelength_A = 1.5406
    for unit in ("counts", "a.u."):
        for p in run_analysis(XRDPattern("p", x, y, unit), recipe).fit.peaks:
            assert x[0] <= p.center_deg <= x[-1]


def test_a_rising_scan_has_no_centre_past_its_end():
    x = np.round(np.arange(20.0, 60.0, 0.02), 6)
    recipe = AnalysisRecipe()
    recipe.preprocess.wavelength_A = 1.5406
    for p in run_analysis(
        XRDPattern("p", x, np.linspace(0, 1e6, x.size), "a.u."), recipe
    ).fit.peaks:
        assert x[0] <= p.center_deg <= x[-1]


@pytest.mark.parametrize("scale", [1e101, 1e300, -1e200])
def test_absurd_intensities_are_refused(tmp_path, scale):
    x = np.arange(20.0, 21.0, 0.01)
    y = scale * np.ones(x.size)
    recipe = AnalysisRecipe()
    recipe.preprocess.wavelength_A = 1.5406
    with pytest.raises(AnalysisError, match="beyond any measured"):
        run_analysis(XRDPattern("p", x, y, "a.u."), recipe)
    path = tmp_path / "scan.csv"
    rows = "\n".join(f"{a:.3f},{b:.6g}" for a, b in zip(x, y, strict=True))
    path.write_text("2theta_deg,intensity_au\n" + rows, encoding="utf-8")
    with pytest.raises(XRDFormatError, match="beyond any measured"):
        read_pattern(path)


@pytest.mark.parametrize("scale", [1e-6, 1e-3, 1.0, 1e3, 1e9])
def test_a_scale_factor_changes_nothing_but_the_scale_in_arbitrary_units(scale):
    x = np.round(np.arange(20.0, 60.0, 0.02), 6)
    y = np.random.default_rng(0).poisson(100 + _peak(x, 30.0, 0.2, 5000.0)).astype(float)
    recipe = AnalysisRecipe()
    recipe.preprocess.wavelength_A = 1.5406
    base = run_analysis(XRDPattern("p", x, y, "a.u."), recipe).fit.peaks
    scaled = run_analysis(XRDPattern("p", x, y * scale, "a.u."), recipe).fit.peaks
    assert [round(p.center_deg, 3) for p in scaled] == [round(p.center_deg, 3) for p in base]
    assert scaled[0].height / scale == pytest.approx(base[0].height, rel=1e-3)


def test_detection_searches_physical_widths_on_fine_scans():
    x = np.arange(20.0, 40.0, 0.0005)
    y = np.random.default_rng(4).poisson(200 + _peak(x, 30.0, 0.15, 30.0)).astype(float)
    net = y - 200.0
    sigma = np.sqrt(np.maximum(y, 1.0))
    cands, _ = detect_peaks(x, net, net, 6.0, 0.15, 0.0, 40, sigma)
    assert [round(c.position_deg) for c in cands] == [30]
    assert cands[0].fwhm_est_deg == pytest.approx(0.15, rel=0.5)


@pytest.mark.parametrize("recorded", ["Cu", "", "1.5406 A"])
def test_a_recorded_wavelength_that_is_not_a_number_is_ignored(recorded):
    x = np.round(np.arange(20.0, 40.0, 0.02), 6)
    y = np.random.default_rng(0).poisson(100 + _peak(x, 30.0, 0.2, 900.0)).astype(float)
    recipe = AnalysisRecipe()
    recipe.preprocess.wavelength_A = 1.5406
    pat = XRDPattern("p", x, y, "counts", metadata={"file_wavelength_A": recorded})
    res = run_analysis(pat, recipe)
    assert res.fit.peaks and not [w for w in res.warnings if "recorded in the file" in w]
