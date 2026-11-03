"""Automatic peak detection (scipy.signal.find_peaks) and reproducible manual edits."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy.signal import find_peaks, peak_widths

from .preprocessing import noise_sigma
from .profiles import kalpha2_position

FloatArray = NDArray[np.float64]
MIN_WIDTH_STEPS = 1.5


@dataclass
class PeakCandidate:
    position_deg: float
    height: float
    fwhm_est_deg: float
    prominence: float
    source: str  # "auto" | "manual"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
    would make the threshold artificially low). Returns (candidates, median threshold).
    """
    step = float(np.median(np.diff(x)))
    span = float(np.max(y) - np.min(y)) or 1.0
    thr: float | FloatArray
    if point_sigma is not None:
        thr = np.maximum(prominence_sigma * np.asarray(point_sigma, dtype=float), 1e-3 * span)
        thr_report = float(np.median(thr))
    else:
        sigma = noise_sigma(noise_reference if noise_reference is not None else y)
        thr = max(prominence_sigma * sigma, 1e-3 * span)
        thr_report = float(thr)
    distance = max(1, round(min_distance_deg / step))
    idx, props = find_peaks(y, height=thr, prominence=thr, distance=distance)
    if idx.size == 0:
        return [], thr_report
    widths = peak_widths(y, idx, rel_height=0.5)[0] * step
    # FWHM below 1.5 sampling steps cannot be a resolvable diffraction peak (noise spike)
    keep = (widths >= min_width_deg) & (widths >= MIN_WIDTH_STEPS * step)
    if max_width_deg is not None:
        keep &= widths <= max_width_deg
    idx, widths, prom = idx[keep], widths[keep], props["prominences"][keep]
    order = np.argsort(prom)[::-1][:max_peaks]
    cands = [
        PeakCandidate(
            position_deg=float(x[idx[i]]),
            height=float(y[idx[i]]),
            fwhm_est_deg=float(max(widths[i], 2 * step)),
            prominence=float(prom[i]),
            source="auto",
        )
        for i in order
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
