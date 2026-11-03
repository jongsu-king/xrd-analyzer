import math

import pytest

from xrd_analyzer.core.crystallography import (
    NOT_CALCULABLE,
    OK,
    WARNING,
    InstrumentBroadening,
    bragg,
    cubic_reflections,
    d_spacing,
    sample_broadening,
    scherrer,
    two_theta_from_d,
    williamson_hall,
)
from xrd_analyzer.models.units import (
    CU_KA1_A,
    SI_LATTICE_A,
    fwhm_deg_to_rad,
    validate_wavelength,
)

SI_111_D = SI_LATTICE_A / math.sqrt(3)


def test_si_111_bragg_known_answer():
    # Si(111), a = 5.431020511 Å, Cu Kα1: d = 3.135601 Å, 2θ ≈ 28.442°
    tt = two_theta_from_d(SI_111_D, CU_KA1_A)
    assert tt == pytest.approx(28.4419, abs=2e-4)
    assert d_spacing(tt, CU_KA1_A) == pytest.approx(SI_111_D, rel=1e-12)


def test_bragg_uses_theta_not_two_theta():
    tt = 28.4419
    correct = CU_KA1_A / (2 * math.sin(math.radians(tt / 2)))
    wrong = CU_KA1_A / (2 * math.sin(math.radians(tt)))  # the classic 2θ/θ mix-up
    assert d_spacing(tt, CU_KA1_A) == pytest.approx(correct)
    assert abs(d_spacing(tt, CU_KA1_A) - wrong) > 1.0


def test_bragg_uncertainty_matches_numeric_derivative():
    tt, s2t = 47.3, 0.002
    res = bragg(tt, CU_KA1_A, s2t)
    h = 1e-6
    num = abs(d_spacing(tt + h, CU_KA1_A) - d_spacing(tt - h, CU_KA1_A)) / (2 * h) * s2t
    assert res.uncertainty == pytest.approx(num, rel=1e-5)
    assert res.unit == "Å" and res.status == OK


def test_bragg_without_position_error_states_reason():
    res = bragg(47.3, CU_KA1_A, None)
    assert res.uncertainty is None and "unavailable" in res.reason


@pytest.mark.parametrize("tt", [0.0, -5.0, 180.0, 200.0])
def test_bragg_rejects_out_of_range(tt):
    with pytest.raises(ValueError):
        d_spacing(tt, CU_KA1_A)


def test_two_theta_from_d_impossible():
    with pytest.raises(ValueError):
        two_theta_from_d(0.5, CU_KA1_A)  # λ/2d > 1


@pytest.mark.parametrize("bad", [None, 0.0, -1.0, 154.0, 15.4, float("nan")])
def test_wavelength_validation(bad):
    with pytest.raises(ValueError):
        validate_wavelength(bad)


def test_fwhm_conversion_is_radians():
    assert float(fwhm_deg_to_rad(1.0)) == pytest.approx(math.pi / 180)


def _measured(D_nm, tt, K=0.9, beta_i=0.05):
    th = math.radians(tt) / 2
    bs = math.degrees(K * CU_KA1_A / (D_nm * 10 * math.cos(th)))
    return math.hypot(bs, beta_i)


def test_scherrer_known_answer_quadratic():
    inst = InstrumentBroadening("constant", 0.05)
    bm = _measured(20.0, 40.0)
    res = scherrer(40.0, bm, 0.001, 0.9, CU_KA1_A, inst, "quadratic")
    assert res.status == OK
    assert res.value == pytest.approx(20.0, rel=1e-10)
    assert res.unit == "nm" and res.uncertainty and res.uncertainty > 0
    assert any("Gaussian" in a for a in res.assumptions)


def test_scherrer_linear_correction():
    inst = InstrumentBroadening("constant", 0.05)
    th = math.radians(20.0)
    bs = math.degrees(0.9 * CU_KA1_A / (300 * math.cos(th)))
    res = scherrer(40.0, bs + 0.05, None, 0.9, CU_KA1_A, inst, "linear")
    assert res.value == pytest.approx(30.0, rel=1e-10)
    assert any("Lorentzian" in a for a in res.assumptions)


def test_scherrer_requires_instrument_and_K():
    none = InstrumentBroadening("none")
    r1 = scherrer(40.0, 0.5, 0.01, 0.9, CU_KA1_A, none)
    assert r1.status == NOT_CALCULABLE and r1.value is None
    assert "instrumental broadening not provided" in r1.reason
    r2 = scherrer(40.0, 0.5, 0.01, None, CU_KA1_A, InstrumentBroadening("constant", 0.05))
    assert r2.status == NOT_CALCULABLE and "K" in r2.reason


def test_scherrer_rejects_width_below_instrument():
    res = scherrer(40.0, 0.04, 0.001, 0.9, CU_KA1_A, InstrumentBroadening("constant", 0.05))
    assert res.status == NOT_CALCULABLE and "no resolvable" in res.reason


def test_scherrer_warnings():
    zero = scherrer(40.0, 0.3, 0.01, 0.9, CU_KA1_A, InstrumentBroadening("constant", 0.0))
    assert zero.status == WARNING and "lower bound" in zero.reason
    close = scherrer(40.0, 0.055, 0.001, 0.9, CU_KA1_A, InstrumentBroadening("constant", 0.05))
    assert close.status == WARNING and "1.2×" in close.reason
    big = scherrer(
        40.0,
        _measured(400, 40.0, beta_i=0.01),
        1e-4,
        0.9,
        CU_KA1_A,
        InstrumentBroadening("constant", 0.01),
    )
    assert big.status == WARNING and "150 nm" in big.reason


def test_sample_broadening_invalid_inputs():
    assert sample_broadening(float("nan"), 0.05, "quadratic", None)[0] is None
    assert sample_broadening(0.2, -0.1, "quadratic", None)[0] is None
    with pytest.raises(ValueError):
        sample_broadening(0.2, 0.05, "cubic", None)


def test_caglioti():
    inst = InstrumentBroadening("caglioti", U=0.004, V=-0.002, W=0.004)
    t = math.tan(math.radians(14.0))
    assert inst.fwhm_at(28.0) == pytest.approx(math.sqrt(0.004 * t * t - 0.002 * t + 0.004))
    assert InstrumentBroadening("caglioti", U=0, V=0, W=-1).fwhm_at(40.0) is None


def _wh_peaks(D_nm=25.0, eps=1e-3, K=0.9, beta_i=0.05, angles=(30, 45, 60, 75, 90, 110)):
    out = []
    for tt in angles:
        th = math.radians(tt) / 2
        bs = K * CU_KA1_A / (D_nm * 10 * math.cos(th)) + 4 * eps * math.tan(th)
        out.append((float(tt), math.hypot(math.degrees(bs), beta_i), 1e-4))
    return out


def test_williamson_hall_known_answer():
    res = williamson_hall(_wh_peaks(), 0.9, CU_KA1_A, InstrumentBroadening("constant", 0.05))
    assert res.status == OK
    assert res.size.value == pytest.approx(25.0, rel=1e-6)
    assert res.strain.value == pytest.approx(1e-3, rel=1e-6)
    assert res.n_peaks == 6 and res.r2 == pytest.approx(1.0)


def test_williamson_hall_needs_three_peaks():
    res = williamson_hall(_wh_peaks()[:2], 0.9, CU_KA1_A, InstrumentBroadening("constant", 0.05))
    assert res.status == NOT_CALCULABLE and "≥ 3" in res.reason


def test_williamson_hall_requires_spread_and_inputs():
    close = _wh_peaks(angles=(40.0, 40.5, 41.0))
    r = williamson_hall(close, 0.9, CU_KA1_A, InstrumentBroadening("constant", 0.05))
    assert r.status == NOT_CALCULABLE and "range" in r.reason
    assert (
        williamson_hall(_wh_peaks(), None, CU_KA1_A, InstrumentBroadening("constant", 0.05)).status
        == NOT_CALCULABLE
    )
    assert (
        williamson_hall(_wh_peaks(), 0.9, CU_KA1_A, InstrumentBroadening("none")).status
        == NOT_CALCULABLE
    )


def test_williamson_hall_negative_slope_and_intercept():
    # widths shrinking with angle → negative slope (non-physical in UDM)
    peaks = [(30.0, 0.50, 1e-4), (60.0, 0.40, 1e-4), (90.0, 0.30, 1e-4), (120.0, 0.25, 1e-4)]
    r = williamson_hall(peaks, 0.9, CU_KA1_A, InstrumentBroadening("constant", 0.01))
    assert r.strain.status == WARNING and "negative slope" in r.reason
    # pure strain broadening, intercept ≈ 0 or negative → size not calculable / poorly determined
    strain_only = _wh_peaks(D_nm=1e9, eps=2e-3)
    r2 = williamson_hall(strain_only, 0.9, CU_KA1_A, InstrumentBroadening("constant", 0.05))
    assert r2.size.status in (NOT_CALCULABLE, WARNING)


def test_williamson_hall_excludes_unresolved_peaks():
    peaks = [*_wh_peaks(), (50.0, 0.01, 1e-4)]
    r = williamson_hall(peaks, 0.9, CU_KA1_A, InstrumentBroadening("constant", 0.05))
    assert r.n_peaks == 6 and len(r.excluded) == 1


def test_cubic_reflections_extinction_rules():
    si = cubic_reflections(SI_LATTICE_A, "diamond", CU_KA1_A, 20, 90)
    assert [r.n_sq for r in si] == [3, 8, 11, 16, 19, 24]
    assert [r.multiplicity for r in si[:4]] == [8, 12, 24, 6]
    fcc = cubic_reflections(4.0786, "F", CU_KA1_A)
    assert [r.n_sq for r in fcc[:6]] == [3, 4, 8, 11, 12, 16]
    bcc = cubic_reflections(3.0, "I", CU_KA1_A)
    assert [r.n_sq for r in bcc[:5]] == [2, 4, 6, 8, 10]
    p = cubic_reflections(4.0, "P", CU_KA1_A)
    assert p[0].n_sq == 1
    nine = next(r for r in p if r.n_sq == 9)
    assert set(nine.hkl.split("/")) == {"300", "221"}
    with pytest.raises(ValueError):
        cubic_reflections(4.0, "X", CU_KA1_A)
