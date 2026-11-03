"""Known-answer validation of the full pipeline on the synthetic ground-truth examples."""

import numpy as np
import pytest

from xrd_analyzer.core.analysis import AnalysisError, run_analysis, run_batch
from xrd_analyzer.core.crystallography import NOT_CALCULABLE
from xrd_analyzer.core.fit import AnalysisCancelled
from xrd_analyzer.core.reference import reference_from_cubic
from xrd_analyzer.models.dataset import XRDPattern
from xrd_analyzer.models.units import CU_KA1_A, CU_KA2_A, SI_LATTICE_A

# Acceptance tolerances (docs/scientific_validation/VALIDATION.md)
TOL_POS_DEG = 0.005
TOL_D_A = 2e-4
TOL_FWHM_REL = 0.06


def _paired(result, truth):
    fits = sorted(
        (p for p in result.peaks if p.fit["center_deg"] == p.fit["center_deg"]),
        key=lambda p: p.fit["center_deg"],
    )
    assert len(fits) == len(truth["peaks"]), [p.fit["center_deg"] for p in fits]
    return list(zip(fits, truth["peaks"], strict=True))


@pytest.mark.parametrize("key", ["si_like", "si_like_ka2", "nano_fcc"])
def test_positions_d_and_widths(examples, key):
    ex = examples[key]
    res = run_analysis(ex.pattern, ex.recipe)
    for rep, tr in _paired(res, ex.truth):
        tol = max(TOL_POS_DEG, 0.01 * tr["fwhm_deg"])  # broad peaks: 1 % of FWHM
        assert rep.fit["center_deg"] == pytest.approx(tr["two_theta_deg"], abs=tol)
        assert rep.d_spacing.value == pytest.approx(tr["d_A"], abs=TOL_D_A)
        assert rep.fit["fwhm_deg"] == pytest.approx(tr["fwhm_deg"], rel=TOL_FWHM_REL)
    assert res.notes[0].startswith("SYNTHETIC")


def test_si_resolution_and_honest_size_refusal(examples):
    ex = examples["si_like"]
    res = run_analysis(ex.pattern, ex.recipe)
    widths = [p.fit["fwhm_deg"] for p in res.peaks]
    assert max(widths) < 0.09  # instrument-limited resolution is reproduced
    assert all(p.scherrer.status == NOT_CALCULABLE for p in res.peaks)
    assert res.williamson_hall.status == NOT_CALCULABLE


def test_nanocrystalline_size_and_strain(examples):
    ex = examples["nano_fcc"]
    res = run_analysis(ex.pattern, ex.recipe)
    wh = res.williamson_hall
    assert wh.size.value == pytest.approx(ex.truth["D_nm"], rel=0.05)
    assert wh.strain.value == pytest.approx(ex.truth["strain"], rel=0.15)
    assert abs(wh.size.value - ex.truth["D_nm"]) < 3 * wh.size.uncertainty
    # Scherrer ignores strain → apparent sizes must be below the true size
    assert all(p.scherrer.value < ex.truth["D_nm"] for p in res.peaks)
    # the amorphous hump at 24° must not be reported as a Bragg peak
    assert all(abs(p.fit["center_deg"] - 24.0) > 3 for p in res.peaks)


def test_kalpha2_strip_mode(examples):
    ex = examples["si_like_ka2"]
    r = ex.recipe.copy()
    r.preprocess.kalpha2_mode = "strip"
    res = run_analysis(ex.pattern, r)
    assert res.processed.ka2_stripped is not None
    for rep, tr in _paired(res, ex.truth):
        assert rep.fit["center_deg"] == pytest.approx(tr["two_theta_deg"], abs=TOL_POS_DEG)


def test_kalpha2_ignored_reports_partners_as_extra_peaks(examples):
    """Without Kα2 handling, resolved Kα2 lines appear as extra 'peaks' whose d-spacings
    (computed with λ1) are wrong — the analysis must say that Kα2 was not handled."""
    ex = examples["si_like_ka2"]
    r = ex.recipe.copy()
    r.preprocess.kalpha2_mode = "none"
    res = run_analysis(ex.pattern, r)
    centres = [p.fit["center_deg"] for p in res.peaks]
    assert len(centres) > len(ex.truth["peaks"])
    assert any(abs(c - 88.303) < 0.01 for c in centres)  # Kα2 of Si(422)
    assert any("Kα2 not handled" in n for n in res.notes)


def test_reference_comparison_in_pipeline(examples):
    ex = examples["si_like"]
    refs = [
        reference_from_cubic("Si (computed)", SI_LATTICE_A, "diamond", CU_KA1_A),
        reference_from_cubic("fcc-like", 4.0786, "F", CU_KA1_A),
    ]
    res = run_analysis(ex.pattern, ex.recipe, references=refs)
    conf = {m.reference: m.confidence for m in res.references}
    assert conf == {"Si (computed)": "high", "fcc-like": "low"}


def test_manual_peak_edits_replay(examples):
    ex = examples["si_like"]
    r = ex.recipe.copy()
    r.peaks.manual_remove_deg = [56.12]
    r.peaks.manual_add_deg = [60.0]
    res = run_analysis(ex.pattern, r)
    centres = [p.fit["center_deg"] for p in res.peaks]
    assert not any(abs(c - 56.12) < 0.05 for c in centres)
    manual = [p for p in res.peaks if p.fit["source"] == "manual"]
    assert len(manual) == 1
    assert manual[0].fit["flags"]  # a manual seed on empty background must be flagged


def test_failures_are_explicit(examples):
    ex = examples["si_like"]
    r = ex.recipe.copy()
    r.preprocess.wavelength_A = None
    with pytest.raises(AnalysisError, match="wavelength is not set"):
        run_analysis(ex.pattern, r)
    r.preprocess.wavelength_A = 15.4
    with pytest.raises(AnalysisError, match="units"):
        run_analysis(ex.pattern, r)
    r = ex.recipe.copy()
    r.fit.profile = "gauss"
    with pytest.raises(AnalysisError, match="invalid recipe"):
        run_analysis(ex.pattern, r)
    x = np.r_[ex.pattern.two_theta_deg, ex.pattern.two_theta_deg[:3]]
    y = np.r_[ex.pattern.intensity, ex.pattern.intensity[:3]]
    dup = XRDPattern("dup", x, y, "counts")
    with pytest.raises(AnalysisError, match="duplicate"):
        run_analysis(dup, ex.recipe)
    r = ex.recipe.copy()
    r.preprocess.duplicate_policy = "average"
    assert run_analysis(dup, r).processed.axis_report.n_duplicates == 3
    with pytest.raises(AnalysisCancelled):
        run_analysis(ex.pattern, ex.recipe, cancel=lambda: True)


def test_non_count_units_and_measured_background(examples):
    ex = examples["si_like"]
    au = XRDPattern("au", ex.pattern.two_theta_deg, ex.pattern.intensity / 10, "a.u.")
    res = run_analysis(au, ex.recipe)
    assert res.processed.sigma is None and len(res.peaks) == 6
    assert any("not raw counts" in n for n in res.notes)
    bg = XRDPattern(
        "holder", ex.pattern.two_theta_deg, np.full(ex.pattern.n_points, 50.0), "counts"
    )
    res2 = run_analysis(ex.pattern, ex.recipe, background=bg)
    assert np.allclose(res2.processed.raw - res2.processed.background_subtracted, 50.0)


@pytest.mark.parametrize("method", ["als", "poly", "none"])
def test_alternative_baselines_and_smoothing(examples, method):
    ex = examples["si_like"]
    r = ex.recipe.copy()
    r.preprocess.baseline_method = method
    r.preprocess.smoothing_enabled = True
    r.preprocess.als_lambda = 1e5
    r.preprocess.poly_degree = 5
    res = run_analysis(ex.pattern, r)
    assert res.processed.smoothed is not None
    if method != "none":
        centres = sorted(p.fit["center_deg"] for p in res.peaks if p.fit["center_err"])
        for tr in ex.truth["peaks"]:
            assert min(abs(c - tr["two_theta_deg"]) for c in centres) < TOL_POS_DEG


def test_voigt_profile_pipeline(examples):
    ex = examples["nano_fcc"]
    r = ex.recipe.copy()
    r.fit.profile = "voigt"
    res = run_analysis(ex.pattern, r)
    for rep, tr in _paired(res, ex.truth):
        assert rep.fit["center_deg"] == pytest.approx(tr["two_theta_deg"], abs=0.02)


def test_batch_continues_after_failure(examples):
    ex = examples["si_like"]
    broken = XRDPattern("broken", [10, 10, 10, 10, 10, 10], [1, 2, 3, 4, 5, 6], "counts")
    seen = []
    items = run_batch(
        [ex.pattern, broken, examples["si_like_ka2"].pattern],
        ex.recipe,
        progress=lambda f, m: seen.append(f),
    )
    assert [i.error is None for i in items] == [True, False, True]
    assert "duplicate" in items[1].error and seen[-1] == 1.0
    with pytest.raises(AnalysisCancelled):
        run_batch([ex.pattern], ex.recipe, cancel=lambda: True)


def test_result_serialisation(examples):
    ex = examples["nano_fcc"]
    res = run_analysis(ex.pattern, ex.recipe)
    d = res.to_dict(include_arrays=True)
    assert (
        d["format"] == "xrd-analyzer-result" and len(d["processed"]["net"]) == ex.pattern.n_points
    )
    assert d["provenance"]["source_sha256"] == ex.pattern.sha256
    assert d["recipe"]["preprocess"]["wavelength_A"] == CU_KA1_A
    assert CU_KA2_A > CU_KA1_A


def test_batch_peak_statistics(examples):
    from xrd_analyzer.core.analysis import batch_peak_statistics

    ex1, ex2 = examples["si_like"], examples["si_like_ka2"]
    r1 = run_analysis(ex1.pattern, ex1.recipe)
    r2 = run_analysis(ex2.pattern, ex2.recipe)
    rows = batch_peak_statistics([r1, r2], tolerance_deg=0.02)
    assert len(rows) == 6 and all(r["n_patterns"] == 2 for r in rows)
    first = rows[0]
    assert first["two_theta_mean_deg"] == pytest.approx(28.4419, abs=2e-3)
    assert first["two_theta_std_deg"] is not None and first["two_theta_std_deg"] < 2e-3
    single = batch_peak_statistics([r1])
    assert single[0]["two_theta_std_deg"] is None


def test_masked_baseline_separates_amorphous_hump(examples):
    import math

    ex = examples["nano_fcc"]
    res = run_analysis(ex.pattern, ex.recipe)
    x = res.processed.two_theta_deg
    true_bg = 150 + 800 * np.exp(-4 * math.log(2) * ((x - 24.0) / 8.0) ** 2)
    hump = (x > 20) & (x < 32)
    assert np.median(np.abs(res.processed.baseline[hump] - true_bg[hump])) < 20
    assert any("masked baseline" in n for n in res.notes)
    snip = ex.recipe.copy()
    snip.preprocess.baseline_method = "snip"
    res_snip = run_analysis(ex.pattern, snip)
    # SNIP erodes the broad hump (documented limitation); masked baseline must do better
    assert np.median(np.abs(res_snip.processed.baseline[hump] - true_bg[hump])) > 100
