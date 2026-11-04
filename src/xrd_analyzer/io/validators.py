"""Sanity checks on parsed patterns. Problems are reported, never silently repaired."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

MIN_POINTS = 5
INTEGER_TOLERANCE = 1e-6


@dataclass(frozen=True)
class Issue:
    level: str  # "error" | "warning"
    code: str
    message: str


def check_arrays(two_theta_deg: NDArray[np.float64], intensity: NDArray[np.float64]) -> list[Issue]:
    x = np.asarray(two_theta_deg, dtype=float)
    y = np.asarray(intensity, dtype=float)
    issues: list[Issue] = []
    if x.size != y.size:
        return [Issue("error", "length_mismatch", "2θ and intensity have different lengths")]
    if x.size < MIN_POINTS:
        return [Issue("error", "too_few_points", f"only {x.size} points (need ≥ {MIN_POINTS})")]
    if not np.all(np.isfinite(x)):
        issues.append(Issue("error", "nonfinite_axis", "2θ contains NaN/Inf"))
    if not np.all(np.isfinite(y)):
        issues.append(Issue("error", "nonfinite_intensity", "intensity contains NaN/Inf"))
    if issues:
        return issues
    if x.min() <= 0.0 or x.max() >= 180.0:
        issues.append(
            Issue(
                "error",
                "axis_out_of_range",
                f"2θ range {x.min():.4g}–{x.max():.4g}° is outside (0°, 180°): "
                "check whether the column is θ, 2θ, radians or a different quantity",
            )
        )
    dx = np.diff(x)
    if np.any(dx == 0):
        issues.append(
            Issue("warning", "duplicate_2theta", f"{int(np.sum(dx == 0))} duplicate 2θ values")
        )
    if np.all(dx < 0):
        issues.append(Issue("warning", "descending_axis", "2θ is in descending order"))
    elif not (np.all(dx >= 0) or np.all(dx <= 0)):
        issues.append(
            Issue(
                "warning", "non_monotonic_axis", "2θ is not monotonic (merged/overlapping scans?)"
            )
        )
    adx = np.abs(dx[dx != 0])
    if adx.size > 2:
        cv = float(np.std(adx) / np.mean(adx))
        if cv > 0.05:
            issues.append(
                Issue("warning", "non_uniform_step", f"2θ step is non-uniform (CV={cv:.2%})")
            )
    if np.any(y < 0):
        issues.append(
            Issue(
                "warning",
                "negative_intensity",
                f"{int(np.sum(y < 0))} negative intensities (pre-subtracted data?)",
            )
        )
    ymax = y.max()
    if ymax > 0:
        at_max = y >= ymax
        run = _longest_run(at_max)
        if run >= 3:
            issues.append(
                Issue(
                    "warning",
                    "possible_saturation",
                    f"{run} consecutive points at the maximum intensity (detector saturation?)",
                )
            )
    if x.max() < 3.2:
        issues.append(
            Issue(
                "warning",
                "axis_looks_like_radians",
                "all 2θ values < 3.2°: confirm the axis unit is degrees, not radians",
            )
        )
    return issues


def non_integer_count(intensity: NDArray[np.float64]) -> int:
    """Number of finite values that are not whole numbers. Raw detector counts always are."""
    y = np.asarray(intensity, dtype=float)
    y = y[np.isfinite(y)]
    return int(np.sum(np.abs(y - np.round(y)) > INTEGER_TOLERANCE))


def _longest_run(mask: NDArray[np.bool_]) -> int:
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return 0
    breaks = np.flatnonzero(np.diff(idx) != 1)
    starts = np.concatenate(([0], breaks + 1))
    ends = np.concatenate((breaks, [idx.size - 1]))
    return int(np.max(ends - starts + 1))


def errors(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.level == "error"]
