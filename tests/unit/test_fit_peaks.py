import numpy as np
import pytest

from xrd_analyzer.core import profiles
from xrd_analyzer.core.fit import AnalysisCancelled, fit_peaks
from xrd_analyzer.core.peaks import (
    PeakCandidate,
    apply_manual_edits,
    detect_peaks,
    drop_kalpha2_partners,
)
from xrd_analyzer.core.uncertainty import (
    covariance_from_jacobian,
    num_grad,
    propagate,
    std_from_cov,
)
from xrd_analyzer.models.units import CU_KA1_A, CU_KA2_A

X = np.round(np.arange(30, 40, 0.01), 6)


def _cand(pos, w=0.1, h=1000.0):
    return PeakCandidate(pos, h, w, h, "auto")


def test_pseudo_voigt_noise_free_exact():
    y = profiles.pseudo_voigt(X, 35.0, 1000, 0.12, 0.4)
    r = fit_peaks(X, y, [_cand(35.02)], local_background="none")
    p = r.peaks[0]
    assert p.center_deg == pytest.approx(35.0, abs=1e-6)
    assert p.fwhm_deg == pytest.approx(0.12, rel=1e-5)
    assert p.eta == pytest.approx(0.4, abs=1e-4)
    assert p.area == pytest.approx(profiles.pseudo_voigt_area(1000, 0.12, 0.4), rel=1e-5)
    assert r.groups[0].r2 == pytest.approx(1.0)


def test_poisson_fit_errors_are_consistent():
    rng = np.random.default_rng(5)
    truth = profiles.pseudo_voigt(X, 35.0, 2000, 0.12, 0.5) + 100
    hits = 0
    for _ in range(20):
        y = rng.poisson(truth).astype(float)
        r = fit_peaks(
            X, y - 100, [_cand(35.0)], sigma=np.sqrt(np.maximum(y, 1)), local_background="constant"
        )
        p = r.peaks[0]
        hits += abs(p.center_deg - 35.0) < 2 * p.center_err
        assert 0.5 < r.groups[0].chi2_red < 2.0
    assert hits >= 16  # ≈95 % coverage expected for ±2σ


def test_voigt_profile_fit():
    y = profiles.voigt(X, 34.0, 50.0, 0.03, 0.04)
    r = fit_peaks(
        X, y, [_cand(34.0, 0.12, float(y.max()))], profile="voigt", local_background="none"
    )
    p = r.peaks[0]
    assert p.sigma_deg == pytest.approx(0.03, rel=1e-3)
    assert p.gamma_deg == pytest.approx(0.04, rel=1e-3)
    assert p.fwhm_deg == pytest.approx(profiles.voigt_fwhm(0.03, 0.04), rel=1e-4)
    assert p.height == pytest.approx(float(y.max()), rel=1e-3)


def test_overlapping_peaks_fitted_jointly():
    y = profiles.pseudo_voigt(X, 35.0, 1000, 0.2, 0.5) + profiles.pseudo_voigt(
        X, 35.3, 600, 0.2, 0.5
    )
    r = fit_peaks(X, y, [_cand(35.0, 0.2), _cand(35.3, 0.2, 600)], local_background="linear")
    assert len(r.groups) == 1 and r.groups[0].peak_ids == [0, 1]
    assert [round(p.center_deg, 4) for p in r.peaks] == [35.0, 35.3]
    model = r.model(X)
    assert np.max(np.abs(model - y)) < 1.0


def test_doublet_fit_reports_kalpha1_position():
    x = np.round(np.arange(65, 73, 0.005), 6)
    c2 = profiles.kalpha2_position(69.13, CU_KA1_A, CU_KA2_A)
    y = profiles.pseudo_voigt(x, 69.13, 1000, 0.07, 0.5) + 0.5 * profiles.pseudo_voigt(
        x, c2, 1000, 0.07, 0.5
    )
    r = fit_peaks(
        x, y, [_cand(69.14, 0.08)], doublet=(CU_KA1_A, CU_KA2_A, 0.5), local_background="none"
    )
    assert r.peaks[0].center_deg == pytest.approx(69.13, abs=1e-5)
    assert r.peaks[0].doublet
    assert np.max(np.abs(r.model(x) - y)) < 1e-3 * 1000


def test_too_few_points_and_empty_and_cancel():
    x = np.arange(30, 30.05, 0.01)
    r = fit_peaks(x, np.ones_like(x), [_cand(30.02, 0.001)])
    assert "fit_skipped" in r.peaks[0].flags and not r.peaks[0].ok
    assert fit_peaks(X, X, []).warnings == ["no peaks to fit"]
    with pytest.raises(AnalysisCancelled):
        fit_peaks(X, X, [_cand(35)], cancel=lambda: True)
    with pytest.raises(ValueError):
        fit_peaks(X, X, [_cand(35)], profile="gauss")


def test_unidentifiable_parameters_are_flagged():
    y = profiles.pseudo_voigt(X, 35.0, 1000, 0.12, 0.5)
    r = fit_peaks(X, y, [_cand(35.0), _cand(35.001)], local_background="none")
    flags = [f for p in r.peaks for f in p.flags] + r.groups[0].flags
    assert any(
        f in flags
        for f in ("parameters_not_identifiable", "ill_conditioned", "covariance_singular")
    )


def test_detect_and_edit_peaks():
    y = sum(profiles.pseudo_voigt(X, c, 1000, 0.1, 0.5) for c in (32, 35, 38))
    y = y + np.random.default_rng(2).normal(0, 5, X.size)
    cands, thr = detect_peaks(X, y, prominence_sigma=10)
    assert [round(c.position_deg) for c in cands] == [32, 35, 38] and thr > 0
    sig = np.full_like(X, 5.0)
    cands2, _ = detect_peaks(X, y, prominence_sigma=10, point_sigma=sig, max_width_deg=0.05)
    assert cands2 == []  # width filter rejects all 0.1° peaks
    edited, notes = apply_manual_edits(
        cands, X, y, add_deg=[33.5, 35.01, 99.0], remove_deg=[38.0], tolerance_deg=0.05
    )
    pos = [round(c.position_deg, 2) for c in edited]
    assert pos == [32.0, 33.5, 35.0]
    assert edited[1].source == "manual"
    assert any("outside" in n for n in notes) and any("duplicates" in n for n in notes)


def test_drop_kalpha2_partners():
    c1 = PeakCandidate(69.13, 1000, 0.07, 1000, "auto")
    c2 = PeakCandidate(profiles.kalpha2_position(69.13, CU_KA1_A, CU_KA2_A), 500, 0.07, 500, "auto")
    kept, n = drop_kalpha2_partners([c1, c2], CU_KA1_A, CU_KA2_A)
    assert n == 1 and kept == [c1]


def test_uncertainty_helpers():
    jac = np.array([[1.0, 0.0], [0.0, 2.0], [1.0, 1.0]])
    cov, cond, flags = covariance_from_jacobian(jac, 1.0, weighted=True)
    assert np.allclose(cov, np.linalg.inv(jac.T @ jac)) and flags == [] and cond > 1
    assert std_from_cov(cov, 0) == pytest.approx(np.sqrt(cov[0, 0]))
    assert std_from_cov(None, 0) is None and propagate(None, [0], np.ones(1)) is None
    sing, _, f2 = covariance_from_jacobian(np.array([[1.0, 1.0], [2.0, 2.0]]), 1.0, True)
    assert sing is None and "covariance_singular" in f2
    g = num_grad(lambda p: p[0] ** 2 + 3 * p[1], np.array([2.0, 1.0]))
    assert np.allclose(g, [4.0, 3.0], rtol=1e-6)
    assert propagate(np.eye(2), [0, 1], np.array([3.0, 4.0])) == pytest.approx(5.0)
