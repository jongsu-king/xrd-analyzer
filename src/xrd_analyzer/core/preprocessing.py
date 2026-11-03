"""Pre-processing: axis ordering, duplicates, measured background, Kα2, smoothing, baseline.

All functions return new arrays; inputs are never modified.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy import sparse
from scipy.signal import savgol_filter
from scipy.sparse.linalg import spsolve

FloatArray = NDArray[np.float64]


class ProcessingError(ValueError):
    pass


@dataclass
class AxisReport:
    sorted: bool = False
    n_duplicates: int = 0
    duplicate_policy: str = "error"
    median_step_deg: float = math.nan
    step_cv: float = math.nan
    notes: list[str] = field(default_factory=list)
    # number of raw observations behind each returned point (duplicate averaging → n > 1)
    n_per_point: NDArray[np.float64] | None = field(default=None, repr=False)

    def update_steps(self, x: FloatArray) -> None:
        steps = np.diff(x)
        if steps.size:
            self.median_step_deg = float(np.median(steps))
            self.step_cv = float(np.std(steps) / np.mean(steps)) if steps.size > 1 else 0.0

    def to_dict(self) -> dict[str, object]:
        return {k: v for k, v in self.__dict__.items() if k != "n_per_point"}


def order_axis(
    x: FloatArray, y: FloatArray, duplicate_policy: str = "error"
) -> tuple[FloatArray, FloatArray, AxisReport]:
    """Sort by 2θ (stable) and handle exactly repeated 2θ values.

    ``duplicate_policy``: ``"error"`` (default, refuse), ``"average"`` (mean intensity of the
    repeated points) or ``"keep_first"`` (first occurrence in file order).
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    rep = AxisReport(duplicate_policy=duplicate_policy)
    order = np.argsort(x, kind="stable")
    if np.any(order != np.arange(x.size)):
        rep.sorted = True
        rep.notes.append("2θ axis was not ascending; data were sorted (raw file unchanged)")
    xs, ys = x[order], y[order]
    uniq, first_idx, counts = np.unique(xs, return_index=True, return_counts=True)
    rep.n_duplicates = int(xs.size - uniq.size)
    if rep.n_duplicates:
        if duplicate_policy == "error":
            dup_vals = uniq[counts > 1][:5]
            raise ProcessingError(
                f"{rep.n_duplicates} duplicate 2θ points (e.g. {np.round(dup_vals, 5).tolist()}). "
                "Choose a duplicate policy ('average' or 'keep_first') explicitly."
            )
        if duplicate_policy == "average":
            inv = np.repeat(np.arange(uniq.size), counts)
            ys = np.bincount(inv, weights=ys) / counts
            rep.n_per_point = counts.astype(float)
        elif duplicate_policy == "keep_first":
            ys = ys[first_idx]
        else:
            raise ProcessingError(f"unknown duplicate policy {duplicate_policy!r}")
        xs = uniq
        rep.notes.append(
            f"{rep.n_duplicates} duplicate 2θ points handled with '{duplicate_policy}'"
        )
    if rep.n_per_point is None:
        rep.n_per_point = np.ones(xs.size)
    rep.update_steps(xs)
    return xs, ys, rep


def crop(
    x: FloatArray, y: FloatArray, lo: float | None, hi: float | None, return_mask: bool = False
) -> Any:
    """Return (x, y) inside [lo, hi]; with ``return_mask`` also the boolean mask."""
    mask = np.ones(x.size, dtype=bool)
    if lo is not None:
        mask &= x >= lo
    if hi is not None:
        mask &= x <= hi
    if mask.sum() < 5:
        raise ProcessingError(f"crop range {lo}–{hi}° leaves fewer than 5 points")
    return (x[mask], y[mask], mask) if return_mask else (x[mask], y[mask])


def interp_with_variance(
    x: FloatArray, bx: FloatArray, by: FloatArray, bvar: FloatArray
) -> tuple[FloatArray, FloatArray]:
    """Linear interpolation of (by, bvar) onto ``x`` for independent samples.

    Value (1−t)·b_j + t·b_{j+1}; variance (1−t)²·v_j + t²·v_{j+1}. No extrapolation.
    """
    bx = np.asarray(bx, dtype=float)
    if float(np.min(x)) < bx[0] - 1e-9 or float(np.max(x)) > bx[-1] + 1e-9:
        raise ProcessingError(
            f"measured background covers {bx[0]:.3f}–{bx[-1]:.3f}° but the pattern spans "
            f"{float(np.min(x)):.3f}–{float(np.max(x)):.3f}°; crop the pattern or provide a wider "
            "background"
        )
    j = np.clip(np.searchsorted(bx, x, side="right") - 1, 0, bx.size - 2)
    t = np.clip((x - bx[j]) / (bx[j + 1] - bx[j]), 0.0, 1.0)
    val = (1 - t) * by[j] + t * by[j + 1]
    var = (1 - t) ** 2 * bvar[j] + t**2 * bvar[j + 1]
    return np.asarray(val, dtype=float), np.asarray(var, dtype=float)


def local_step(x: FloatArray, center: float, half_window: int = 5) -> float:
    """Median sampling step around ``center`` (robust for non-uniform grids)."""
    i = int(np.searchsorted(x, center))
    seg = x[max(i - half_window, 0) : min(i + half_window + 1, x.size)]
    d = np.diff(seg)
    return float(np.median(d)) if d.size else float(np.median(np.diff(x)))


def subtract_measured_background(
    x: FloatArray, y: FloatArray, bx: FloatArray, by: FloatArray, scale: float = 1.0
) -> FloatArray:
    """Subtract a measured (empty holder / dark) pattern, linearly interpolated onto ``x``.

    The background must cover the whole 2θ range; extrapolation is refused.
    """
    bx = np.asarray(bx, dtype=float)
    by = np.asarray(by, dtype=float)
    order = np.argsort(bx)
    bx, by = bx[order], by[order]
    xlo, xhi, blo, bhi = float(np.min(x)), float(np.max(x)), float(np.min(bx)), float(np.max(bx))
    if xlo < blo - 1e-9 or xhi > bhi + 1e-9:
        raise ProcessingError(
            f"measured background covers {blo:.3f}–{bhi:.3f}° but the pattern spans "
            f"{xlo:.3f}–{xhi:.3f}°; crop the pattern or provide a wider background"
        )
    return y - scale * np.interp(x, bx, by)


def strip_kalpha2(
    x: FloatArray, y: FloatArray, lambda1_A: float, lambda2_A: float, ratio: float = 0.5
) -> FloatArray:
    """Rachinger Kα2 stripping.

    Assumes the Kα2 component is an exact copy of the Kα1 profile scaled by ``ratio`` and
    shifted to the 2θ given by Bragg's law with λ2. For each point at 2θ the Kα1 angle that
    produces Kα2 intensity there is 2θ₁ = 2·asin(sin(θ)·λ1/λ2) < 2θ, so the profile can be
    built in ascending order: I₁(2θ) = I(2θ) − R·I₁(2θ₁).
    """
    if not lambda2_A > lambda1_A > 0:
        raise ProcessingError("Kα2 stripping requires λ2 > λ1 > 0")
    if not 0 < ratio < 1:
        raise ProcessingError("Kα2/Kα1 ratio must lie in (0, 1)")
    theta = np.deg2rad(x) / 2.0
    x1 = np.rad2deg(2.0 * np.arcsin(np.sin(theta) * lambda1_A / lambda2_A))
    out = np.array(y, dtype=float, copy=True)
    idx = np.searchsorted(x, x1, side="right") - 1  # x[idx] <= x1 < x[idx+1]
    for i in range(x.size):
        j = int(idx[i])
        if j < 0:
            # Kα1 partner below the scan start: assume a locally flat profile, for which
            # I = I1·(1 + R) exactly. This avoids an artificial step at the first points.
            out[i] = y[i] / (1.0 + ratio)
        elif j + 1 >= i:
            # partner between x[i-1] and x[i]: solve the interpolation for out[i] exactly
            t = (x1[i] - x[j]) / (x[i] - x[j]) if i > j else 1.0
            out[i] = (y[i] - ratio * out[j] * (1 - t)) / (1.0 + ratio * t)
        else:
            t = (x1[i] - x[j]) / (x[j + 1] - x[j])
            out[i] = y[i] - ratio * (out[j] * (1 - t) + out[j + 1] * t)
    return out


def savgol(y: FloatArray, window: int, polyorder: int) -> FloatArray:
    if window % 2 == 0 or window < 3:
        raise ProcessingError("Savitzky–Golay window must be odd and ≥ 3")
    if polyorder >= window:
        raise ProcessingError("Savitzky–Golay polyorder must be smaller than the window")
    if window > y.size:
        raise ProcessingError(f"Savitzky–Golay window {window} exceeds data length {y.size}")
    return np.asarray(savgol_filter(y, window, polyorder, mode="interp"), dtype=float)


# ------------------------------------------------------------------------------ baselines


def baseline_snip(y: FloatArray, iterations: int = 40) -> FloatArray:
    """SNIP baseline (Ryan et al., NIM B 34, 396 (1988)) with LLS transform.

    ``iterations`` ≈ half-width (in points) of the widest feature still treated as a peak.
    """
    if iterations < 1:
        raise ProcessingError("SNIP iterations must be ≥ 1")
    offset = min(0.0, float(np.min(y)))
    v = np.log(np.log(np.sqrt(y - offset + 1.0) + 1.0) + 1.0)
    n = v.size
    for k in range(1, min(iterations, (n - 1) // 2) + 1):
        avg = 0.5 * (v[: n - 2 * k] + v[2 * k :])
        v[k : n - k] = np.minimum(v[k : n - k], avg)
    b = (np.exp(np.exp(v) - 1.0) - 1.0) ** 2 - 1.0 + offset
    return np.asarray(b, dtype=float)


def baseline_als(
    y: FloatArray, lam: float = 1e6, p: float = 0.01, iterations: int = 15
) -> FloatArray:
    """Asymmetric least squares baseline (Eilers & Boelens, 2005)."""
    if not 0 < p < 1 or lam <= 0:
        raise ProcessingError("ALS needs λ > 0 and 0 < p < 1")
    n = y.size
    d = sparse.diags([1.0, -2.0, 1.0], [0, -1, -2], shape=(n, n - 2))
    penalty = lam * (d @ d.T)
    w = np.ones(n)
    z = y
    for _ in range(iterations):
        wm = sparse.spdiags(w, 0, n, n)
        z = spsolve(sparse.csc_matrix(wm + penalty), w * y)
        w = p * (y > z) + (1 - p) * (y < z)
    return np.asarray(z, dtype=float)


def baseline_masked_whittaker(
    y: FloatArray, mask: NDArray[np.bool_], lam: float = 1e6
) -> FloatArray:
    """Whittaker smoother fitted only through points outside ``mask`` (Bragg-peak regions).

    z = (W + λ·DᵀD)⁻¹·W·y with W = 0 inside the mask: broad features such as an amorphous
    hump stay in the baseline, while masked peaks (and their tails) cannot pull it up.
    Across masked gaps the second-difference penalty interpolates smoothly.
    """
    if lam <= 0:
        raise ProcessingError("Whittaker λ must be > 0")
    mask = np.asarray(mask, dtype=bool)
    if mask.mean() > 0.8:
        raise ProcessingError("peak mask covers > 80 % of the pattern; baseline is undetermined")
    n = y.size
    d = sparse.diags([1.0, -2.0, 1.0], [0, -1, -2], shape=(n, n - 2))
    w = (~mask).astype(float)
    z = spsolve(sparse.csc_matrix(sparse.spdiags(w, 0, n, n) + lam * (d @ d.T)), w * y)
    return np.asarray(z, dtype=float)


def peak_mask(
    x: FloatArray, centers: list[float], fwhms: list[float], factor: float
) -> NDArray[np.bool_]:
    m = np.zeros(x.size, dtype=bool)
    for c, w in zip(centers, fwhms, strict=True):
        m |= np.abs(x - c) <= factor * w
    return m


def baseline_poly(
    x: FloatArray, y: FloatArray, degree: int = 4, iterations: int = 100
) -> FloatArray:
    """Iterative modified polynomial baseline (Lieber & Mahadevan-Jansen, 2003)."""
    if degree < 0 or degree > 12:
        raise ProcessingError("polynomial degree must be 0–12")
    xs = (x - x.mean()) / (np.ptp(x) or 1.0)
    work = np.array(y, dtype=float, copy=True)
    fit = work
    for _ in range(iterations):
        coef = np.polynomial.polynomial.polyfit(xs, work, degree)
        fit = np.polynomial.polynomial.polyval(xs, coef)
        new = np.minimum(work, fit)
        if np.allclose(new, work, rtol=0, atol=1e-9 * (np.abs(work).max() + 1)):
            break
        work = new
    return np.asarray(fit, dtype=float)


def noise_sigma(y: FloatArray) -> float:
    """Robust noise estimate: MAD of first differences / (0.6745·√2)."""
    d = np.diff(np.asarray(y, dtype=float))
    if d.size == 0:
        return 0.0
    mad = float(np.median(np.abs(d - np.median(d))))
    return mad / 0.6745 / math.sqrt(2.0)


def local_noise_sigma(y: FloatArray, window: int = 201) -> FloatArray:
    """Point-wise robust noise σ: rolling median of |Δy| / (0.6745·√2).

    Used for peak detection when intensities are not raw counts (no Poisson σ), so that a
    noisier region does not produce false peaks under a single global threshold.
    """
    from scipy.ndimage import median_filter

    d = np.abs(np.diff(np.asarray(y, dtype=float)))
    if d.size == 0:
        return np.zeros_like(np.asarray(y, dtype=float))
    w = max(3, min(window, d.size) | 1)
    loc = median_filter(d, size=w, mode="nearest") / 0.6745 / math.sqrt(2.0)
    loc = np.concatenate([loc, loc[-1:]])
    floor = max(float(np.median(loc)) * 1e-3, 1e-12)
    return np.asarray(np.maximum(loc, floor), dtype=float)


def baseline_warnings(y: FloatArray, baseline: FloatArray) -> list[str]:
    """Flag obviously bad baselines (overshoot into the data, large negative net intensity)."""
    sigma = noise_sigma(y) or 1e-12
    net = y - baseline
    warn: list[str] = []
    frac_over = float(np.mean(net < -3 * sigma))
    if frac_over > 0.05:
        warn.append(
            f"baseline lies > 3σ above the data at {frac_over:.0%} of points "
            "(over-fitting / wrong parameters?)"
        )
    if np.any(~np.isfinite(baseline)):
        warn.append("baseline contains non-finite values")
    return warn
