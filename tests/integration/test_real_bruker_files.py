"""Comparison with real Bruker files. Real research data are not committed to this repository.

Point XRD_REAL_DATA_DIR at a folder holding ``X.brml`` and its text export ``X_exported.xy``
from the same scan. Without it these tests are skipped.
"""

import os
from pathlib import Path

import numpy as np
import pytest

from xrd_analyzer.core.analysis import run_analysis
from xrd_analyzer.io import AxisConfirmationRequired, ReadOptions, probe_file, read_pattern
from xrd_analyzer.models.dataset import VERIFIED_NATIVE
from xrd_analyzer.models.recipe import AnalysisRecipe

ROOT = os.environ.get("XRD_REAL_DATA_DIR")
PAIRS = (
    [(b, b.with_name(b.stem + "_exported.xy")) for b in sorted(Path(ROOT).glob("*.brml"))]
    if ROOT
    else []
)
pytestmark = pytest.mark.skipif(not PAIRS, reason="XRD_REAL_DATA_DIR not set")
AXIS = {"axis_quantity": "2theta", "axis_unit": "deg"}


@pytest.mark.parametrize("pair", PAIRS, ids=lambda p: p[0].stem)
def test_brml_matches_vendor_text_export(pair):
    brml, xy = pair
    native = read_pattern(brml)
    export = np.loadtxt(xy, skiprows=1)
    time_s = float(native.metadata["time_per_step_s"])
    assert native.format_status == VERIFIED_NATIVE
    assert native.n_points == export.shape[0]
    assert np.abs(native.two_theta_deg - export[:, 0]).max() < 1e-5
    # the export holds count rates rounded to three decimals
    assert np.abs(native.intensity / time_s - export[:, 1]).max() <= 5.01e-4


@pytest.mark.parametrize("pair", PAIRS, ids=lambda p: p[0].stem)
def test_text_export_reads_exactly_and_is_recognised(pair):
    _, xy = pair
    pat = read_pattern(xy, ReadOptions(**AXIS, intensity_unit="cps"))
    export = np.loadtxt(xy, skiprows=1)
    assert np.array_equal(pat.two_theta_deg, export[:, 0])
    assert np.array_equal(pat.intensity, export[:, 1])
    assert pat.format_status == VERIFIED_NATIVE
    probe = probe_file(xy)
    assert not probe.integer_intensity and probe.suggested_intensity_unit == "cps"
    with pytest.raises(AxisConfirmationRequired, match="not whole numbers"):
        read_pattern(xy, ReadOptions(**AXIS, intensity_unit="counts"))


@pytest.mark.parametrize("pair", PAIRS, ids=lambda p: p[0].stem)
def test_both_routes_find_the_same_peaks(pair):
    brml, xy = pair
    native = read_pattern(brml)
    r = AnalysisRecipe()
    r.preprocess.wavelength_A = float(native.metadata["file_wavelength_A"])
    r.preprocess.wavelength2_A = float(native.metadata["file_wavelength2_A"])
    r.preprocess.kalpha2_mode = "fit_doublet"
    counts = run_analysis(native, r)
    rate = run_analysis(read_pattern(xy, ReadOptions(**AXIS, intensity_unit="cps")), r)
    a = np.array([p.center_deg for p in counts.fit.peaks])
    b = np.array([p.center_deg for p in rate.fit.peaks])
    # the two noise models may disagree about the weakest peaks only; the strong ones must match
    strong = [p.center_deg for p in counts.fit.peaks if p.significance and p.significance > 12]
    assert strong
    assert all(np.abs(b - c).min() < 0.01 for c in strong)
    assert abs(a.size - b.size) <= 2
