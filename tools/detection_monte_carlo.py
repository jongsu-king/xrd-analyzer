"""False-positive and recall rates of peak detection on Poisson scans with known content.

Usage: python tools/detection_monte_carlo.py [scans per condition, default 300]
Results for each release are recorded in docs/scientific_validation/VALIDATION.md.
"""

import sys

import numpy as np

from xrd_analyzer.core import profiles
from xrd_analyzer.core.analysis import run_analysis
from xrd_analyzer.models.dataset import XRDPattern
from xrd_analyzer.models.recipe import AnalysisRecipe

N_SCANS = int(sys.argv[1]) if len(sys.argv) > 1 else 300
X_FINE = np.round(np.arange(5.0, 50.0, 0.0101), 5)
X_COARSE = np.round(np.arange(20.0, 100.0, 0.02), 5)


def recipe(baseline):
    r = AnalysisRecipe()
    r.preprocess.wavelength_A = 1.5406
    r.preprocess.baseline_method = baseline
    return r


def real_shaped(x, level):
    """Low-angle upturn, broad glass hump and slow decay, as in the first real scans tested."""
    upturn = 3.5 * np.exp(-(x - x[0]) / 0.45)
    hump = 0.55 * np.exp(-(((x - 26) / 7.0) ** 2))
    return level * (0.65 + upturn + hump - 0.006 * (x - 5))


def run(name, x, background, unit, baseline, scans, peak=None):
    rng = np.random.default_rng(12345)
    false_peaks, scans_with_false, found = 0, 0, 0
    mean = background if peak is None else background + peak
    for _ in range(scans):
        pattern = XRDPattern("mc", x, rng.poisson(np.maximum(mean, 0)).astype(float), unit)
        result = run_analysis(pattern, recipe(baseline))
        centers = np.array([p.center_deg for p in result.fit.peaks if p.ok])
        hit = np.abs(centers - 30.0) < 0.1 if peak is not None else np.zeros(centers.size, bool)
        found += int(hit.any())
        false_peaks += int((~hit).sum())
        scans_with_false += int((~hit).any())
    recall = f"  found {found / scans:6.1%}" if peak is not None else ""
    rate = false_peaks / scans
    print(f"{name:42s} false/scan {rate:6.3f}  scans with any {scans_with_false:4d}{recall}")


half = max(N_SCANS // 2, 20)
print("noise only: every reported peak is false")
for level in (10, 80, 1000):
    flat = np.full_like(X_FINE, level, dtype=float)
    run(f"flat {level} counts, step 0.01, snip", X_FINE, flat, "counts", "snip", N_SCANS)
run(
    "flat 80 counts, step 0.02, snip",
    X_COARSE,
    np.full_like(X_COARSE, 80.0),
    "counts",
    "snip",
    N_SCANS,
)
run(
    "flat 80 counts, step 0.01, masked",
    X_FINE,
    np.full_like(X_FINE, 80.0),
    "counts",
    "masked",
    half,
)
shaped = real_shaped(X_FINE, 80)
run("real-shaped background, snip", X_FINE, shaped, "counts", "snip", N_SCANS)
run("real-shaped background, masked", X_FINE, shaped, "counts", "masked", half)
run("real-shaped background, a.u.", X_FINE, shaped, "a.u.", "snip", N_SCANS)

flat80 = np.full_like(X_FINE, 80.0)
for fwhm, heights in ((0.1, (20, 30, 40, 60, 80, 120)), (0.25, (15, 25, 40))):
    print(f"one known peak at 30° on 80 counts per step, FWHM {fwhm}°, η 0.5")
    for h in heights:
        peak = profiles.pseudo_voigt(X_FINE, 30.0, float(h), fwhm, 0.5)
        label = f"height {h} counts ({h / np.sqrt(80):.1f}σ per point)"
        run(label, X_FINE, flat80, "counts", "snip", half, peak)
