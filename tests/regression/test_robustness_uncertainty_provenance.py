"""Robustness regressions: integer/finite validation, uncertainty-model qualification,
background provenance and replay, weak and narrow Kα doublets."""

import math

import numpy as np
import pytest

from xrd_analyzer.core import profiles
from xrd_analyzer.core.analysis import run_analysis
from xrd_analyzer.core.crystallography import (
    NOT_CALCULABLE,
    InstrumentBroadening,
    bragg,
    williamson_hall,
)
from xrd_analyzer.core.fit import fit_peaks
from xrd_analyzer.core.peaks import PeakCandidate
from xrd_analyzer.models.dataset import XRDPattern
from xrd_analyzer.models.recipe import AnalysisRecipe
from xrd_analyzer.models.units import CU_KA1_A, CU_KA2_A
from xrd_analyzer.project import (
    Project,
    ProjectError,
    entry_for,
    load_project,
    resolve_background,
    save_project,
)


def _recipe(**pre):
    r = AnalysisRecipe()
    r.preprocess.wavelength_A = CU_KA1_A
    for k, v in pre.items():
        setattr(r.preprocess, k, v)
    return r


def test_wh_and_bragg_reject_nonfinite_wavelength():
    peaks = [(30.0, 0.3, 1e-3), (50.0, 0.4, 1e-3), (70.0, 0.5, 1e-3), (90.0, 0.6, 1e-3)]
    inst = InstrumentBroadening("constant", 0.05)
    assert williamson_hall(peaks, 0.9, math.nan, inst).status == NOT_CALCULABLE
    assert williamson_hall(peaks, 0.9, -1.0, inst).status == NOT_CALCULABLE
    b = bragg(30.0, math.nan, 0.001)
    assert b.status == NOT_CALCULABLE and b.value is None


@pytest.mark.parametrize(
    "section, field, value",
    [
        ("preprocess", "snip_iterations", 2.5),
        ("peaks", "max_peaks", 2.5),
        ("preprocess", "smoothing_window", 3.5),
        ("preprocess", "smoothing_polyorder", 1.5),
        ("size", "wh_min_peaks", True),
    ],
)
def test_integer_fields_require_integers(section, field, value):
    r = AnalysisRecipe()
    setattr(getattr(r, section), field, value)
    assert any(f"{section}.{field} must be an integer" in e for e in r.validate())


def test_strip_qualified_for_every_unit(examples):
    ex = examples["si_like_ka2"]
    cps = XRDPattern("cps", ex.pattern.two_theta_deg, ex.pattern.intensity / 2.0, "cps")
    r = ex.recipe.copy()
    r.preprocess.kalpha2_mode = "strip"
    res = run_analysis(cps, r)
    assert any("correlates the noise" in w for w in res.warnings)
    assert "approximate" in res.processed.uncertainty_model[0]
    assert res.to_dict()["uncertainty_model"] == res.processed.uncertainty_model


def test_background_interpolation_correlation_is_qualified(examples):
    xb = np.arange(20.0, 30.001, 0.1)
    xs = np.round(np.arange(20.05, 29.96, 0.1), 6)
    pat = XRDPattern("s", xs, np.full(xs.size, 100.0), "counts")
    bg = XRDPattern("b", xb, np.full(xb.size, 100.0), "counts")
    res = run_analysis(pat, _recipe(baseline_method="none"), background=bg)
    assert any("interpolated" in m and "correlat" in m for m in res.processed.uncertainty_model)
    same = XRDPattern("b2", xs, np.full(xs.size, 10.0), "counts")
    res2 = run_analysis(pat, _recipe(baseline_method="none"), background=same)
    assert not any("interpolated" in m for m in res2.processed.uncertainty_model)
    ex = examples["si_like"]
    plain = run_analysis(ex.pattern, ex.recipe)
    assert plain.processed.uncertainty_model[0].startswith("Poisson weights")


def test_array_hash_distinguishes_import_choices(tmp_path, examples):
    x = np.linspace(10.0, 40.0, 301)
    as_2t = XRDPattern("bg", x, np.full(x.size, 30.0), "counts", sha256="same-file")
    as_t = XRDPattern(
        "bg",
        2 * x,
        np.full(x.size, 30.0),
        "counts",
        sha256="same-file",
        quality_flags=("axis_converted_from_theta",),
    )
    ex = examples["si_like"]
    r = ex.recipe.copy()
    r.preprocess.crop_min_deg, r.preprocess.crop_max_deg = 25.0, 39.0
    p1 = run_analysis(ex.pattern, r, background=as_2t).provenance["measured_background"]
    p2 = run_analysis(ex.pattern, r, background=as_t).provenance["measured_background"]
    assert p1["sha256"] == p2["sha256"] and p1["array_sha256"] != p2["array_sha256"]
    assert p2["quality_flags"] == ["axis_converted_from_theta"]
    d = run_analysis(ex.pattern, ex.recipe).to_dict()
    assert d["provenance"]["source_array_sha256"] == d["dataset"]["array_sha256"]


def test_background_round_trips_through_project(tmp_path, examples):
    ex = examples["si_like"]
    bg = XRDPattern(
        "holder", ex.pattern.two_theta_deg, np.full(ex.pattern.n_points, 20.0), "counts"
    )
    e = entry_for(ex.pattern, ex.recipe, None, example_key="si_like", background=bg)
    path = save_project(Project([e]), tmp_path / "p.json")
    back = resolve_background(load_project(path).entries[0])
    assert back is not None and back.sha256 == bg.sha256
    assert np.array_equal(back.intensity, bg.intensity)
    e.background["intensity"][0] += 1.0
    with pytest.raises(ProjectError, match="background hash mismatch"):
        resolve_background(e)


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_weak_unresolved_doublet_is_kept(seed):
    x = np.round(np.arange(28.0, 32.0, 0.005), 6)
    c2 = profiles.kalpha2_position(30.0, CU_KA1_A, CU_KA2_A)
    y = (
        100
        + profiles.pseudo_voigt(x, 30.0, 80, 0.3, 0.5)
        + profiles.pseudo_voigt(x, c2, 40, 0.3, 0.5)
    )
    pat = XRDPattern("weak", x, np.random.default_rng(seed).poisson(y).astype(float), "counts")
    r = _recipe(baseline_method="none", kalpha2_mode="fit_doublet", wavelength2_A=CU_KA2_A)
    res = run_analysis(pat, r)
    assert len(res.peaks) == 1
    assert res.peaks[0].fit["center_deg"] == pytest.approx(30.0, abs=0.02)
    assert res.peaks[0].fit["height"] == pytest.approx(80, rel=0.15)


@pytest.mark.parametrize("seed", [None, 0, 1, 2])
def test_narrow_doublet_is_kept(seed):
    """a converged narrow doublet (FWHM 0.001°) must not be rejected."""
    x = np.round(np.arange(29.9, 30.15, 0.0001), 9)
    c2 = profiles.kalpha2_position(30.0, CU_KA1_A, CU_KA2_A)
    y = (
        100
        + profiles.pseudo_voigt(x, 30.0, 100, 0.001, 0.5)
        + profiles.pseudo_voigt(x, c2, 50, 0.001, 0.5)
    )
    if seed is not None:
        y = np.random.default_rng(seed).poisson(y).astype(float)
    pat = XRDPattern("narrow", x, y, "counts")
    r = _recipe(baseline_method="none", kalpha2_mode="fit_doublet", wavelength2_A=CU_KA2_A)
    res = run_analysis(pat, r)
    assert len(res.peaks) == 1
    assert res.peaks[0].fit["center_deg"] == pytest.approx(30.0, abs=0.001)


def test_doublet_significance_counts_both_components():
    from xrd_analyzer.core.analysis import integrated_significance

    x = np.round(np.arange(25.0, 35.0, 0.01), 4)
    d = (CU_KA1_A, CU_KA2_A, 0.5)
    c2 = profiles.kalpha2_position(30.0, d[0], d[1])
    truth = profiles.pseudo_voigt(x, 30.0, 40.0, 0.1, 0.5)
    y = truth + 0.5 * profiles.pseudo_voigt(x, c2, 40.0, 0.1, 0.5)
    cand = [PeakCandidate(30.0, 40.0, 0.1, 40.0, "auto")]
    sigma = np.full_like(x, 5.0)
    both = fit_peaks(x, y, cand, sigma=sigma, doublet=d, local_background="none")
    single = fit_peaks(x, truth, cand, sigma=sigma, local_background="none")
    with_ka2 = integrated_significance(both.peaks[0], both, x, sigma)
    without = integrated_significance(single.peaks[0], single, x, sigma)
    assert with_ka2 > without
    # an exact fit leaves no residual, so √Δχ² is simply the signal in units of σ
    assert with_ka2 == pytest.approx(np.sqrt(np.sum((y / sigma) ** 2)), rel=0.01)
    assert without == pytest.approx(np.sqrt(np.sum((truth / sigma) ** 2)), rel=0.01)
