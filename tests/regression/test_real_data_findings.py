"""Problems found by the first test on real instrument files, reproduced with synthetic data."""

import json

import numpy as np
import pytest

from xrd_analyzer.cli import main as cli_main
from xrd_analyzer.core import preprocessing as proc
from xrd_analyzer.core import profiles
from xrd_analyzer.core.analysis import run_analysis
from xrd_analyzer.core.fit import fit_peaks
from xrd_analyzer.core.peaks import PeakCandidate, detect_peaks
from xrd_analyzer.io import AxisConfirmationRequired, ReadOptions, probe_file, read_pattern
from xrd_analyzer.models.dataset import SYNTHETIC_ONLY, VERIFIED_NATIVE, XRDPattern
from xrd_analyzer.models.recipe import AnalysisRecipe
from xrd_analyzer.models.units import CU_KA1_A, CU_KA2_A
from xrd_analyzer.reports.exports import write_peak_table_csv

AXIS = {"axis_quantity": "2theta", "axis_unit": "deg"}
BRUKER_HEADER = (
    '\'Id: "Sample" Comment: "" Operator: "Lab" Anode: "Cu" Wavelength: "1.5406" '
    'Scantype: "2Theta (Coupled TwoTheta/Theta)" TimePerStep: "28.548" '
    'X: "9999" Y: "9999" Z: "9999" \n'
)
X = np.round(np.arange(5.0, 50.0, 0.0101), 5)


def _recipe(**preprocess):
    r = AnalysisRecipe()
    r.preprocess.wavelength_A = CU_KA1_A
    for k, v in preprocess.items():
        setattr(r.preprocess, k, v)
    return r


def _noisy(mean, seed=1):
    return np.random.default_rng(seed).poisson(mean).astype(float)


def _bruker_xy(write_text, t=28.548, n=200):
    x = 5.0 + 0.0101 * np.arange(n)
    counts = _noisy(np.full(n, 80.0))
    rows = "\n".join(f"{a:.8f} {b / t:.3f}" for a, b in zip(x, counts, strict=True))
    return write_text("scan_exported.xy", BRUKER_HEADER + rows + "\n")


# ------------------------------------------------------------------ count rates vs counts


def test_fractional_values_cannot_be_counts(write_text):
    p = write_text("a.csv", "2theta_deg,counts\n" + "\n".join(f"{20 + i},{i}.5" for i in range(9)))
    with pytest.raises(AxisConfirmationRequired, match="not whole numbers"):
        read_pattern(p)
    assert read_pattern(p, ReadOptions(intensity_unit="a.u.")).intensity_unit == "a.u."


def test_whole_values_written_with_decimals_are_counts(write_text):
    p = write_text(
        "a.csv", "2theta_deg,counts\n" + "\n".join(f"{20 + i},{i}.000" for i in range(9))
    )
    assert read_pattern(p).counts_like


def test_rate_labelled_counts_through_the_api_warns():
    y = _noisy(np.full(X.size, 80.0)) / 28.548
    res = run_analysis(XRDPattern("p", X, y, "counts"), _recipe())
    assert any("not whole numbers" in w for w in res.warnings)


def test_chi2_far_below_one_is_flagged():
    x = np.linspace(30, 40, 1001)
    y = profiles.pseudo_voigt(x, 35.0, 1000.0, 0.2, 0.5) + np.random.default_rng(0).normal(
        0, 1, 1001
    )
    fit = fit_peaks(
        x, y, [PeakCandidate(35.0, 1000.0, 0.2, 1000.0, "auto")], sigma=np.full(1001, 10.0)
    )
    assert any("chi2_red<0.5" in f for f in fit.groups[0].flags)


def test_small_windows_do_not_trigger_the_low_chi2_flag():
    x = np.linspace(34.9, 35.1, 21)
    y = profiles.pseudo_voigt(x, 35.0, 1000.0, 0.05, 0.5)
    fit = fit_peaks(
        x, y, [PeakCandidate(35.0, 1000.0, 0.05, 1000.0, "auto")], sigma=np.full(21, 10.0)
    )
    assert not any("chi2_red<0.5" in f for f in fit.groups[0].flags)


# ------------------------------------------------------------------ Bruker text export


def test_bruker_header_becomes_metadata(write_text):
    pat = read_pattern(_bruker_xy(write_text), ReadOptions(**AXIS, intensity_unit="cps"))
    assert pat.metadata["Anode"] == "Cu" and pat.metadata["TimePerStep"] == "28.548"
    assert pat.metadata["file_wavelength_A"] == "1.5406"
    assert "'Id" not in pat.metadata
    assert pat.format_status == VERIFIED_NATIVE


def test_bruker_header_still_needs_axis_confirmation(write_text):
    with pytest.raises(AxisConfirmationRequired):
        read_pattern(_bruker_xy(write_text))


def test_count_rate_is_suggested_only_when_the_values_prove_it(write_text):
    probe = probe_file(_bruker_xy(write_text))
    assert not probe.integer_intensity and probe.suggested_intensity_unit == "cps"
    rows = "\n".join(f"{5 + 0.01 * i:.3f} {3.1416 + 0.1234 * i:.4f}" for i in range(50))
    other = probe_file(write_text("o.xy", BRUKER_HEADER + rows + "\n"))
    assert not other.integer_intensity and other.suggested_intensity_unit is None
    plain = "\n".join(f"{5 + 0.01 * i:.3f} {i}" for i in range(50))
    headerless = probe_file(write_text("p.xy", plain + "\n"))
    assert headerless.integer_intensity and headerless.suggested_intensity_unit is None


def test_other_text_files_keep_their_status(write_text):
    p = write_text("a.csv", "2theta_deg,counts\n" + "\n".join(f"{20 + i},{i}" for i in range(9)))
    assert read_pattern(p).format_status == SYNTHETIC_ONLY


def test_quoted_pairs_need_at_least_two_to_split_a_comment(write_text):
    rows = "\n".join(f"{20 + i},{i}" for i in range(9))
    p = write_text("a.csv", '# note: "one value" stays whole\n2theta_deg,counts\n' + rows)
    assert read_pattern(p).metadata["note"] == '"one value" stays whole'


# ------------------------------------------------------------------ detection


def _with_peak(height, fwhm, seed, center=30.0, level=80.0):
    return _noisy(level + profiles.pseudo_voigt(X, center, height, fwhm, 0.5), seed)


@pytest.mark.parametrize("seed", range(5))
def test_weak_peak_with_no_significant_point_is_found(seed):
    # 4.5σ per point but spread over 25 points: overwhelming as a whole
    res = run_analysis(XRDPattern("p", X, _with_peak(40.0, 0.25, seed), "counts"), _recipe())
    assert any(abs(p.center_deg - 30.0) < 0.05 for p in res.fit.peaks)
    assert all(abs(p.center_deg - 30.0) < 0.05 for p in res.fit.peaks)


def test_detection_does_not_depend_on_how_finely_the_scan_is_sampled():
    # the same peak and total counts, sampled twice as finely: each point has half the counts
    x = np.round(np.arange(20.0, 40.0, 0.01), 5)
    coarse = _noisy(160 + profiles.pseudo_voigt(x, 30.0, 60.0, 0.2, 0.5), 3)
    xf = np.round(np.arange(20.0, 40.0, 0.005), 5)
    fine = _noisy(80 + profiles.pseudo_voigt(xf, 30.0, 30.0, 0.2, 0.5), 3)
    for xx, yy in ((x, coarse), (xf, fine)):
        res = run_analysis(XRDPattern("p", xx, yy, "counts"), _recipe())
        assert any(abs(p.center_deg - 30.0) < 0.05 for p in res.fit.peaks)


@pytest.mark.parametrize("seed", range(10))
@pytest.mark.parametrize("unit", ["counts", "a.u."])
def test_noise_alone_gives_no_peaks(seed, unit):
    res = run_analysis(XRDPattern("p", X, _noisy(np.full(X.size, 80.0), seed), unit), _recipe())
    assert res.fit.peaks == []


def test_low_angle_upturn_is_not_a_peak():
    mean = 60 + 280 * np.exp(-(X - X[0]) / 0.45)
    for seed in range(5):
        res = run_analysis(XRDPattern("p", X, _noisy(mean, seed), "counts"), _recipe())
        assert res.fit.peaks == []


def test_tail_of_a_strong_peak_is_not_a_second_peak():
    y = _with_peak(5000.0, 0.08, 7)
    res = run_analysis(XRDPattern("p", X, y, "counts"), _recipe())
    assert [round(p.center_deg) for p in res.fit.peaks] == [30]


def test_weak_peak_seed_has_its_real_width():
    # the seed sets the fit window, so a noise spike must not make it far too narrow
    y = _with_peak(40.0, 0.25, 11)
    net = y - proc.baseline_snip(y, 40)
    cands, _ = detect_peaks(X, net, net, 6.0, 0.15, 0.0, 40, np.sqrt(np.maximum(y, 1)))
    seed = next(c for c in cands if abs(c.position_deg - 30.0) < 0.1)
    assert 0.1 < seed.fwhm_est_deg < 0.5


def test_candidate_cap_is_reported():
    x = np.round(np.arange(20.0, 40.0, 0.01), 5)
    y = 50 + sum(profiles.pseudo_voigt(x, c, 500.0, 0.1, 0.5) for c in np.arange(21.0, 39.0, 1.0))
    r = _recipe()
    r.peaks.max_peaks = 5
    res = run_analysis(XRDPattern("p", x, _noisy(y), "counts"), r)
    assert any("max_peaks" in w for w in res.warnings)


def test_threshold_must_be_positive():
    r = _recipe()
    r.peaks.prominence_sigma = 0.0
    assert any("significance threshold" in e for e in r.validate())


def test_significance_is_reported_and_exported(tmp_path):
    res = run_analysis(XRDPattern("p", X, _with_peak(200.0, 0.1, 2), "counts"), _recipe())
    peak = res.fit.peaks[0]
    assert peak.significance is not None and peak.significance > 6
    csv = write_peak_table_csv(res, tmp_path / "p.csv").read_text(encoding="utf-8")
    assert "significance_sigma [σ]" in csv


# ------------------------------------------------------------------ baselines


@pytest.mark.parametrize("method", ["snip", "als", "poly"])
def test_baseline_follows_the_mean_of_the_noise(method):
    x = np.linspace(20, 80, 3001)
    mean = 80 + 30 * np.exp(-(((x - 45) / 15) ** 2))
    y = _noisy(mean + profiles.pseudo_voigt(x, 50, 2000, 0.15, 0.5))
    b = {
        "snip": lambda: proc.baseline_snip(y, 40),
        "als": lambda: proc.baseline_als(y, 1e6, 0.01, 15),
        "poly": lambda: proc.baseline_poly(x, y, 6, 100),
    }[method]()
    far = np.abs(x - 50) > 2
    assert abs(float(np.mean((y - b)[far]))) < 0.3 * np.sqrt(80)


def test_snip_keeps_no_plateau_near_the_scan_start():
    y = _noisy(60 + 280 * np.exp(-(X - X[0]) / 0.45))
    net = y - proc.baseline_snip(y, 40)
    start, later = (X > X[0] + 0.2) & (X < X[0] + 0.6), (X > 7) & (X < 12)
    assert abs(float(net[start].mean()) - float(net[later].mean())) < 2 * np.sqrt(60)


def test_snip_still_recovers_noise_free_data():
    y = (
        100.0
        + 2.0 * np.linspace(0, 1, 2000)
        + profiles.pseudo_voigt(np.linspace(0, 20, 2000), 10, 500, 0.1, 0.5)
    )
    b = proc.baseline_snip(y, 40)
    assert np.all(np.abs((y - b)[:800]) < 1.0)


# ------------------------------------------------------------------ what results say


def _asymmetric_counts():
    left = profiles.pseudo_voigt(X, 30.0, 4000.0, 0.14, 0.5)
    right = profiles.pseudo_voigt(X, 30.0, 4000.0, 0.07, 0.5)
    return _noisy(80 + np.where(X < 30.0, left, right), 5)


@pytest.mark.parametrize("unit", ["counts", "a.u."])
def test_poor_fit_is_flagged_on_the_peak_in_both_noise_models(unit):
    res = run_analysis(XRDPattern("p", X, _asymmetric_counts(), unit), _recipe())
    assert any("poor_profile_fit" in p.flags for p in res.fit.peaks)
    assert any("poor_profile_fit" in r["flags"] for r in res.peak_rows())


def test_poor_fit_is_not_diluted_by_a_wide_window():
    # a weak neighbour must not pull the strong asymmetric peak into a window full of good points
    y = _asymmetric_counts()
    y = y + _noisy(profiles.pseudo_voigt(X, 28.3, 45.0, 0.25, 0.5) + 1e-9, 8)
    res = run_analysis(XRDPattern("p", X, y, "counts"), _recipe())
    strong = next(p for p in res.fit.peaks if abs(p.center_deg - 30.0) < 0.1)
    assert "poor_profile_fit" in strong.flags
    group = next(g for g in res.fit.groups if g.group_id == strong.group_id)
    assert group.hi_deg - group.lo_deg < 1.5


def test_badly_seeded_width_is_redone_from_the_fit():
    res = run_analysis(XRDPattern("p", X, _with_peak(45.0, 0.09, 13), "counts"), _recipe())
    peak = next(p for p in res.fit.peaks if abs(p.center_deg - 30.0) < 0.1)
    seed = next(c for c in res.candidates if abs(c.position_deg - 30.0) < 0.1)
    assert 1 / 1.5 < peak.fwhm_deg / seed.fwhm_est_deg < 1.5 * 1.5


def test_good_fit_is_not_flagged():
    res = run_analysis(XRDPattern("p", X, _with_peak(2000.0, 0.1, 6), "counts"), _recipe())
    assert not any("poor_profile_fit" in p.flags for p in res.fit.peaks)


def test_shape_parameter_at_its_bound_is_flagged():
    x = np.linspace(30, 40, 1001)
    gaussian = profiles.pseudo_voigt(x, 35.0, 1000.0, 0.2, 0.0)
    fit = fit_peaks(
        x, gaussian, [PeakCandidate(35.0, 1000.0, 0.2, 1000.0, "auto")], local_background="none"
    )
    assert "shape_at_bound" in fit.peaks[0].flags


def test_uncertainty_model_says_sigma_is_statistical_only():
    res = run_analysis(XRDPattern("p", X, _with_peak(500.0, 0.1, 2), "counts"), _recipe())
    assert any("statistical" in m and "displacement" in m for m in res.processed.uncertainty_model)


def test_variance_model_matches_the_noise_model_used():
    y = _with_peak(500.0, 0.1, 2)
    counts = run_analysis(XRDPattern("p", X, y, "counts"), _recipe())
    rate = run_analysis(XRDPattern("p", X, y, "cps"), _recipe())
    strip = run_analysis(
        XRDPattern("p", X, y, "counts"),
        _recipe(kalpha2_mode="strip", wavelength2_A=CU_KA2_A),
    )
    assert counts.provenance["algorithms"]["variance_model"].startswith("Poisson")
    assert rate.provenance["algorithms"]["variance_model"].startswith("unweighted (cps)")
    assert strip.provenance["algorithms"]["variance_model"].startswith("unweighted after")


def test_doublet_results_say_they_are_kalpha1_only(tmp_path):
    pat = XRDPattern("p", X, _with_peak(800.0, 0.1, 2), "counts")
    res = run_analysis(pat, _recipe(kalpha2_mode="fit_doublet", wavelength2_A=CU_KA2_A))
    assert any("Kα1 component only" in n and "1.5 × area" in n for n in res.notes)
    csv = write_peak_table_csv(res, tmp_path / "p.csv").read_text(encoding="utf-8")
    assert "# doublet fit: height and area are Kα1 only; whole reflection = 1.5 × area" in csv
    plain = run_analysis(pat, _recipe())
    assert not any("Kα1 component only" in n for n in plain.notes)


# ------------------------------------------------------------------ CLI


def _csv(write_text):
    y = _with_peak(40.0, 0.25, 4)
    rows = "\n".join(f"{a},{int(b)}" for a, b in zip(X, y, strict=True))
    return write_text("scan.csv", "two_theta_deg,intensity_counts\n" + rows)


def test_cli_exposes_threshold_and_smoothing(write_text, tmp_path):
    out = tmp_path / "o"
    code = cli_main(
        [
            "analyze", str(_csv(write_text)), "--wavelength", "1.5406", "--no-pdf",
            "--prominence-sigma", "4.5", "--smooth", "--out", str(out),
        ]
    )  # fmt: skip
    assert code == 0
    recipe = json.loads((out / "scan_result.json").read_text(encoding="utf-8"))["recipe"]
    assert recipe["peaks"]["prominence_sigma"] == 4.5
    assert recipe["preprocess"]["smoothing_enabled"] is True


def test_cli_cu_preset_fills_in_kalpha2(write_text, tmp_path):
    out = tmp_path / "o"
    args = ["analyze", str(_csv(write_text)), "--wavelength", "cu_ka1", "--ka2", "fit_doublet"]
    assert cli_main([*args, "--no-pdf", "--out", str(out)]) == 0
    recipe = json.loads((out / "scan_result.json").read_text(encoding="utf-8"))["recipe"]
    assert recipe["preprocess"]["wavelength2_A"] == CU_KA2_A


def test_cli_custom_wavelength_still_needs_kalpha2(write_text, tmp_path, capsys):
    args = ["analyze", str(_csv(write_text)), "--wavelength", "1.5406", "--ka2", "fit_doublet"]
    assert cli_main([*args, "--no-pdf", "--out", str(tmp_path / "o")]) == 3
    assert "Kα2 wavelength" in capsys.readouterr().err


def test_cli_explicit_kalpha2_wins_over_the_preset(write_text, tmp_path):
    out = tmp_path / "o"
    args = ["analyze", str(_csv(write_text)), "--wavelength", "cu_ka1", "--ka2", "fit_doublet"]
    assert cli_main([*args, "--wavelength2", "1.5444", "--no-pdf", "--out", str(out)]) == 0
    recipe = json.loads((out / "scan_result.json").read_text(encoding="utf-8"))["recipe"]
    assert recipe["preprocess"]["wavelength2_A"] == 1.5444


def test_cli_rejects_rate_labelled_counts(write_text, tmp_path, capsys):
    code = cli_main(
        [
            "analyze", str(_bruker_xy(write_text)), "--axis", "2theta-deg",
            "--intensity-unit", "counts", "--wavelength", "1.5406", "--no-pdf",
            "--out", str(tmp_path / "o"),
        ]
    )  # fmt: skip
    assert code == 2
    assert "not whole numbers" in capsys.readouterr().err
