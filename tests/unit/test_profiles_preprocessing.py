import math

import numpy as np
import pytest
from scipy.integrate import trapezoid

from xrd_analyzer.core import preprocessing as proc
from xrd_analyzer.core import profiles
from xrd_analyzer.models.units import CU_KA1_A, CU_KA2_A


def _measured_fwhm(x, y):
    half = y.max() / 2
    above = np.flatnonzero(y >= half)
    i0, i1 = above[0], above[-1]
    left = np.interp(half, [y[i0 - 1], y[i0]], [x[i0 - 1], x[i0]])
    right = np.interp(half, [y[i1 + 1], y[i1]], [x[i1 + 1], x[i1]])
    return right - left


@pytest.mark.parametrize("eta", [0.0, 0.3, 1.0])
def test_pseudo_voigt_fwhm_and_area(eta):
    x = np.linspace(20, 40, 200001)
    y = profiles.pseudo_voigt(x, 30.0, 100.0, 0.2, eta)
    assert y.max() == pytest.approx(100.0)
    assert _measured_fwhm(x, y) == pytest.approx(0.2, rel=1e-4)
    tail = 1 - 2 / math.pi * math.atan(2 * 10 / 0.2)  # Lorentzian mass beyond ±10°
    lorentz_area = 100 * 0.2 * math.pi / 2
    expected = profiles.pseudo_voigt_area(100, 0.2, eta) - eta * lorentz_area * tail
    assert trapezoid(y, x) == pytest.approx(expected, rel=1e-5)


def test_pseudo_voigt_area_gradient():
    g = profiles.pseudo_voigt_area_grad(100.0, 0.2, 0.4)
    h = 1e-6
    num = [
        (
            profiles.pseudo_voigt_area(100 + h, 0.2, 0.4)
            - profiles.pseudo_voigt_area(100 - h, 0.2, 0.4)
        )
        / (2 * h),
        (
            profiles.pseudo_voigt_area(100, 0.2 + h, 0.4)
            - profiles.pseudo_voigt_area(100, 0.2 - h, 0.4)
        )
        / (2 * h),
        (
            profiles.pseudo_voigt_area(100, 0.2, 0.4 + h)
            - profiles.pseudo_voigt_area(100, 0.2, 0.4 - h)
        )
        / (2 * h),
    ]
    assert np.allclose(g, num, rtol=1e-6)


def test_voigt_fwhm_approximation_and_start_values():
    x = np.linspace(-5, 5, 400001)
    y = profiles.voigt(x, 0.0, 1.0, 0.05, 0.07)
    assert _measured_fwhm(x, y) == pytest.approx(profiles.voigt_fwhm(0.05, 0.07), rel=5e-4)
    a, s, g = profiles.voigt_params_from_fwhm(0.3, 50.0)
    assert profiles.voigt_fwhm(s, g) == pytest.approx(0.3, rel=1e-3)
    assert profiles.voigt_height(a, s, g) == pytest.approx(50.0)


def test_kalpha2_position():
    c2 = profiles.kalpha2_position(28.4419, CU_KA1_A, CU_KA2_A)
    assert 28.50 < c2 < 28.52
    assert math.isnan(profiles.kalpha2_position(179.9, CU_KA1_A, CU_KA2_A))


def test_order_axis_sorts_and_reports():
    x = np.array([30.0, 20.0, 25.0, 10.0, 15.0])
    y = np.arange(5.0)
    xs, ys, rep = proc.order_axis(x, y)
    assert xs.tolist() == [10, 15, 20, 25, 30] and ys.tolist() == [3, 4, 1, 2, 0]
    assert rep.sorted and rep.n_duplicates == 0


def test_duplicate_policies():
    x = np.array([10.0, 11.0, 11.0, 12.0, 13.0, 14.0])
    y = np.array([1.0, 2.0, 4.0, 5.0, 6.0, 7.0])
    with pytest.raises(proc.ProcessingError, match="duplicate"):
        proc.order_axis(x, y, "error")
    xa, ya, ra = proc.order_axis(x, y, "average")
    assert xa.tolist() == [10, 11, 12, 13, 14] and ya[1] == 3.0 and ra.n_duplicates == 1
    _, yk, _ = proc.order_axis(x, y, "keep_first")
    assert yk[1] == 2.0
    with pytest.raises(proc.ProcessingError):
        proc.order_axis(x, y, "median")


def test_crop_and_errors():
    x = np.linspace(10, 20, 101)
    xc, yc = proc.crop(x, x, 12, 14)
    assert xc[0] >= 12 and xc[-1] <= 14 and yc.size == xc.size
    with pytest.raises(proc.ProcessingError):
        proc.crop(x, x, 12.0, 12.01)


def test_measured_background_subtraction():
    x = np.linspace(20, 30, 101)
    bx = np.linspace(19, 31, 25)
    out = proc.subtract_measured_background(x, 100 + x, bx, 10 + bx, scale=1.0)
    assert np.allclose(out, 90.0)
    with pytest.raises(proc.ProcessingError, match="covers"):
        proc.subtract_measured_background(x, x, np.linspace(21, 31, 10), np.ones(10))


def test_kalpha2_stripping_removes_doublet():
    x = np.round(np.arange(20, 80, 0.005), 6)
    y = np.zeros_like(x)
    for c in (28.44, 47.30, 69.13):
        c2 = profiles.kalpha2_position(c, CU_KA1_A, CU_KA2_A)
        y += profiles.pseudo_voigt(x, c, 1000, 0.06, 0.5)
        y += 0.5 * profiles.pseudo_voigt(x, c2, 1000, 0.06, 0.5)
    stripped = proc.strip_kalpha2(x, y, CU_KA1_A, CU_KA2_A, 0.5)
    truth = sum(profiles.pseudo_voigt(x, c, 1000, 0.06, 0.5) for c in (28.44, 47.30, 69.13))
    assert np.max(np.abs(stripped - truth)) < 0.02 * 1000
    flat = proc.strip_kalpha2(x, np.full_like(x, 300.0), CU_KA1_A, CU_KA2_A, 0.5)
    assert np.allclose(flat, 200.0)  # I1 = I/(1+R) for a flat profile: no edge step
    with pytest.raises(proc.ProcessingError):
        proc.strip_kalpha2(x, y, CU_KA2_A, CU_KA1_A)
    with pytest.raises(proc.ProcessingError):
        proc.strip_kalpha2(x, y, CU_KA1_A, CU_KA2_A, 1.5)


def test_savgol_preserves_polynomial_and_validates():
    x = np.linspace(0, 1, 101)
    y = 3 * x**2 - x + 2
    assert np.allclose(proc.savgol(y, 11, 3), y)
    for w, o in [(10, 3), (1, 0), (11, 11), (201, 3)]:
        with pytest.raises(proc.ProcessingError):
            proc.savgol(y, w, o)


@pytest.mark.parametrize("noise", [0.0, 5.0])
@pytest.mark.parametrize("method", ["snip", "als", "poly"])
def test_baselines_recover_smooth_background(method, noise):
    x = np.linspace(20, 80, 3001)
    bg = 200 + 2 * (x - 20) + 300 * np.exp(-(((x - 25) / 10) ** 2))
    peaks = sum(profiles.pseudo_voigt(x, c, 2000, 0.15, 0.5) for c in (35, 50, 65))
    y = bg + peaks + np.random.default_rng(3).normal(0, noise, x.size)
    if method == "snip":
        b = proc.baseline_snip(y, 40)
    elif method == "als":
        b = proc.baseline_als(y, 1e5, 0.001, 20)
    else:
        b = proc.baseline_poly(x, y, 6, 200)
    far = np.all([np.abs(x - c) > 2 for c in (35, 50, 65)], axis=0)
    # lower-envelope estimators sit ~1–3σ below the mean background on noisy data
    tol = 0.05 * 200 if noise == 0 else 3 * noise
    assert np.median(np.abs(b[far] - bg[far])) < tol
    if noise:
        assert proc.baseline_warnings(y, b) == []


def test_baseline_parameter_validation_and_overshoot_warning():
    y = np.ones(50)
    with pytest.raises(proc.ProcessingError):
        proc.baseline_snip(y, 0)
    with pytest.raises(proc.ProcessingError):
        proc.baseline_als(y, -1, 0.1)
    with pytest.raises(proc.ProcessingError):
        proc.baseline_poly(np.arange(50.0), y, 20)
    noisy = np.random.default_rng(0).normal(100, 1, 500)
    assert proc.baseline_warnings(noisy, noisy + 50)


def test_noise_sigma_estimate():
    y = np.random.default_rng(1).normal(0, 3.0, 20000)
    assert proc.noise_sigma(y) == pytest.approx(3.0, rel=0.05)
    assert proc.noise_sigma(np.array([1.0])) == 0.0


def test_local_noise_sigma_tracks_heteroscedastic_noise():
    rng = np.random.default_rng(4)
    y = np.r_[rng.normal(0, 1, 5000), rng.normal(0, 10, 5000)]
    loc = proc.local_noise_sigma(y, 201)
    assert np.median(loc[:4000]) == pytest.approx(1.0, rel=0.1)
    assert np.median(loc[6000:]) == pytest.approx(10.0, rel=0.1)


def test_masked_whittaker_ignores_masked_peaks():
    x = np.linspace(20, 80, 3001)
    bg = 200 + 300 * np.exp(-(((x - 30) / 8) ** 2))
    y = bg + profiles.pseudo_voigt(x, 50, 3000, 0.8, 0.5)
    # ±8 FWHM: the Lorentzian tail left outside the mask is < 0.5 % of the peak height
    mask = proc.peak_mask(x, [50.0], [0.8], 8.0)
    b = proc.baseline_masked_whittaker(y, mask, 1e5)
    assert np.max(np.abs(b - bg)) < 10.0
    with pytest.raises(proc.ProcessingError, match="80"):
        proc.baseline_masked_whittaker(y, np.ones_like(x, dtype=bool), 1e5)
    with pytest.raises(proc.ProcessingError):
        proc.baseline_masked_whittaker(y, mask, 0)
