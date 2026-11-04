"""Automatic peak detection (scipy.signal.find_peaks) and reproducible manual edits.

A peak spread over many sampling steps can be highly significant as a whole while no single
point stands out, so detection searches single points and Gaussian-smoothed copies of the data.
At every scale the threshold is k × the σ of that (smoothed) signal.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import gaussian_filter1d
from scipy.signal import find_peaks, peak_widths

from .preprocessing import block_means, coarsening_factor, noise_sigma
from .profiles import kalpha2_position

FloatArray = NDArray[np.float64]
MIN_WIDTH_STEPS = 1.5
# Gaussian smoothing widths searched besides single points (FWHM, in sampling steps).
SEARCH_FWHM_STEPS = (4.0, 8.0, 16.0, 32.0)
_FWHM_PER_SIGMA = 2.0 * math.sqrt(2.0 * math.log(2.0))
# A feature wider than this many kernel widths belongs to a coarser scale or to the baseline.
MAX_WIDTH_PER_KERNEL = 3.0
# A single-point peak this many times over threshold keeps its own position and width.
STRONG_PEAK_FACTOR = 2.0


@dataclass
class PeakCandidate:
    position_deg: float
    height: float
    fwhm_est_deg: float
    prominence: float
    source: str  # "auto" | "manual"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _Hit:
    position_deg: float
    height: float
    width_deg: float
    prominence: float
    significance: float  # prominence in units of the σ of the searched signal
    exclusion_deg: float  # no second candidate this close
    strong: bool = False


def _pointwise_hits(
    x: FloatArray,
    y: FloatArray,
    thr: float | FloatArray,
    sigma: FloatArray,
    distance: int,
    step: float,
    min_distance_deg: float,
) -> list[_Hit]:
    idx, props = find_peaks(y, height=thr, prominence=thr, distance=distance)
    if idx.size == 0:
        return []
    widths = peak_widths(y, idx, rel_height=0.5)[0] * step
    thr_at = np.broadcast_to(thr, y.shape)[idx]
    return [
        _Hit(
            float(x[i]),
            float(y[i]),
            float(w),
            float(p),
            float(p / max(sigma[i], 1e-12)),
            min_distance_deg,
            bool(p >= STRONG_PEAK_FACTOR * t),
        )
        for i, w, p, t in zip(idx, widths, props["prominences"], thr_at, strict=True)
    ]


def _smoothed_hits(
    x: FloatArray,
    y: FloatArray,
    sigma: FloatArray,
    k: float,
    floor: float,
    distance: int,
    step: float,
    kernel_steps: float,
    min_distance_deg: float,
) -> list[_Hit]:
    s = kernel_steps / _FWHM_PER_SIGMA
    smooth = gaussian_filter1d(y, s, mode="nearest")
    # Var(Σ gᵢyᵢ) = Σ gᵢ²σᵢ², and g² is a Gaussian of width s/√2 with area 1/(2s√π).
    var = gaussian_filter1d(sigma**2, s / math.sqrt(2.0), mode="nearest") / (
        2.0 * s * math.sqrt(math.pi)
    )
    sig = np.sqrt(var)
    thr = np.maximum(k * sig, floor)
    idx, props = find_peaks(smooth, height=thr, prominence=thr, distance=distance)
    if idx.size == 0:
        return []
    measured = peak_widths(smooth, idx, rel_height=0.5)[0]
    hits = []
    for i, w, p in zip(idx, measured, props["prominences"], strict=True):
        if w > MAX_WIDTH_PER_KERNEL * kernel_steps:
            continue
        if i < kernel_steps or i >= y.size - kernel_steps:
            continue  # the kernel reaches past the scan end
        # remove the kernel from the width; never below half of it, where noise dominates
        width = max(math.sqrt(max(w**2 - kernel_steps**2, 0.0)), 0.5 * kernel_steps) * step
        hits.append(
            _Hit(
                float(x[i]),
                float(smooth[i]),
                width,
                float(p),
                float(p / max(sig[i], 1e-12)),
                max(min_distance_deg, kernel_steps * step),
            )
        )
    return hits


def detect_peaks(
    x: FloatArray,
    y: FloatArray,
    noise_reference: FloatArray | None = None,
    prominence_sigma: float = 6.0,
    min_distance_deg: float = 0.15,
    min_width_deg: float = 0.0,
    max_peaks: int = 40,
    point_sigma: FloatArray | None = None,
    max_width_deg: float | None = None,
) -> tuple[list[PeakCandidate], float]:
    """Find peaks whose net height **and** prominence exceed ``prominence_sigma`` × σ.

    σ is the per-point Poisson σ (``point_sigma``, raw counts) when available, otherwise a
    robust global estimate from ``noise_reference`` (the unsmoothed net signal — smoothing
    ``y`` would make the single-point threshold artificially low). The same test then runs on
    Gaussian-smoothed copies of ``noise_reference``, each against the σ of its own smoothed
    signal. Candidates are seeds for the fit, which decides whether they are confirmed.
    Returns (candidates, median single-point threshold).
    """
    step = float(np.median(np.diff(x)))
    span = float(np.max(y) - np.min(y)) or 1.0
    floor = 1e-3 * span
    thr: float | FloatArray
    if point_sigma is not None:
        sigma = np.asarray(point_sigma, dtype=float)
        thr = np.maximum(prominence_sigma * sigma, floor)
        thr_report = float(np.median(thr))
    else:
        sigma = np.full_like(y, noise_sigma(noise_reference if noise_reference is not None else y))
        thr = max(prominence_sigma * float(sigma[0]), floor)
        thr_report = float(thr)
    distance = max(1, round(min_distance_deg / step))
    source = noise_reference if noise_reference is not None else y
    hits = _pointwise_hits(x, y, thr, sigma, distance, step, min_distance_deg)
    for kernel in SEARCH_FWHM_STEPS:
        hits += _smoothed_hits(
            x, source, sigma, prominence_sigma, floor, distance, step, kernel, min_distance_deg
        )
    # The kernels above count steps. A finely sampled scan needs the same widths in degrees too.
    factor = coarsening_factor(x)
    if factor > 1:
        xb, source_b = block_means(x, factor), block_means(source, factor)
        sigma_b = np.sqrt(block_means(sigma**2, factor) / factor)
        step_b = factor * step
        for kernel in SEARCH_FWHM_STEPS:
            hits += _smoothed_hits(
                xb,
                source_b,
                sigma_b,
                prominence_sigma,
                floor,
                max(1, round(min_distance_deg / step_b)),
                step_b,
                kernel,
                min_distance_deg,
            )

    # FWHM below 1.5 sampling steps cannot be a resolvable diffraction peak (noise spike)
    def usable(h: _Hit) -> bool:
        return (
            h.width_deg >= min_width_deg
            and h.width_deg >= MIN_WIDTH_STEPS * step
            and (max_width_deg is None or h.width_deg <= max_width_deg)
        )

    hits = [h for h in hits if usable(h)]
    # Strong peaks keep their single-point position and width. Every other feature takes the
    # scale at which it is most significant, which is the one matching its width.
    accepted = [h for h in hits if h.strong]
    for h in sorted((h for h in hits if not h.strong), key=lambda h: -h.significance):
        radius = h.exclusion_deg
        if all(abs(h.position_deg - a.position_deg) >= radius for a in accepted):
            accepted.append(h)
    accepted.sort(key=lambda h: -h.prominence)
    cands = [
        PeakCandidate(
            position_deg=h.position_deg,
            height=h.height,
            fwhm_est_deg=float(max(h.width_deg, 2 * step)),
            prominence=h.prominence,
            source="auto",
        )
        for h in accepted[:max_peaks]
    ]
    cands.sort(key=lambda c: c.position_deg)
    return cands, thr_report


def drop_kalpha2_partners(
    candidates: list[PeakCandidate],
    lambda1_A: float,
    lambda2_A: float,
    max_ratio: float = 0.8,
) -> tuple[list[PeakCandidate], int]:
    """Remove auto-detected candidates that sit at the Kα2 position of a stronger peak.

    Used when Kα1/Kα2 doublets are fitted, so each reflection is seeded only once.
    """
    drop: set[int] = set()
    for i, c in enumerate(candidates):
        c2 = kalpha2_position(c.position_deg, lambda1_A, lambda2_A)
        tol = max(0.5 * c.fwhm_est_deg, 0.02)
        for j, o in enumerate(candidates):
            if (
                j != i
                and o.source == "auto"
                and abs(o.position_deg - c2) <= tol
                and o.height <= max_ratio * c.height
            ):
                drop.add(j)
    return [c for k, c in enumerate(candidates) if k not in drop], len(drop)


def apply_manual_edits(
    candidates: list[PeakCandidate],
    x: FloatArray,
    y: FloatArray,
    add_deg: list[float],
    remove_deg: list[float],
    tolerance_deg: float = 0.05,
) -> tuple[list[PeakCandidate], list[str]]:
    """Remove candidates near ``remove_deg`` and add manual seeds at ``add_deg``.

    Edits are stored in the recipe, so they replay identically on re-analysis.
    """
    notes: list[str] = []
    kept = [
        c
        for c in candidates
        if not any(abs(c.position_deg - r) <= tolerance_deg for r in remove_deg)
    ]
    removed = len(candidates) - len(kept)
    if removed:
        notes.append(f"{removed} peak(s) removed manually")
    step = float(np.median(np.diff(x)))
    default_w = float(np.median([c.fwhm_est_deg for c in kept])) if kept else 10 * step
    for pos in add_deg:
        if not x[0] <= pos <= x[-1]:
            notes.append(f"manual peak at {pos:.4f}° is outside the data range — ignored")
            continue
        if any(abs(c.position_deg - pos) <= tolerance_deg for c in kept):
            notes.append(f"manual peak at {pos:.4f}° duplicates an existing peak — ignored")
            continue
        h = float(np.interp(pos, x, y))
        kept.append(PeakCandidate(pos, h, default_w, float("nan"), "manual"))
    kept.sort(key=lambda c: c.position_deg)
    return kept, notes
