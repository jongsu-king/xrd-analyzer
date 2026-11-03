"""Covariance and first-order (delta-method) uncertainty propagation helpers."""

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np
from numpy.typing import NDArray

COND_LIMIT = 1e10


def covariance_from_jacobian(
    jac: NDArray[np.float64], chi2_red: float, weighted: bool
) -> tuple[NDArray[np.float64] | None, float, list[str]]:
    """cov = (JᵀJ)⁻¹ via SVD, scaled by max(1, χ²_red) (weighted) or χ²_red (unweighted).

    Returns (cov or None if singular, condition number, flags).
    """
    flags: list[str] = []
    try:
        _, s, vt = np.linalg.svd(jac, full_matrices=False)
    except np.linalg.LinAlgError:
        return None, math.inf, ["covariance_singular"]
    cond = float(s[0] / s[-1]) if s[-1] > 0 else math.inf
    if not math.isfinite(cond) or s[-1] <= np.finfo(float).eps * s[0] * max(jac.shape):
        return None, cond, ["covariance_singular"]
    cov = (vt.T / s**2) @ vt
    scale = max(1.0, chi2_red) if weighted else chi2_red
    cov = cov * (scale if math.isfinite(scale) else 1.0)
    if cond > COND_LIMIT:
        flags.append("ill_conditioned")
    return cov, cond, flags


def std_from_cov(cov: NDArray[np.float64] | None, k: int) -> float | None:
    if cov is None:
        return None
    v = cov[k, k]
    return float(math.sqrt(v)) if v >= 0 and math.isfinite(v) else None


def num_grad(
    f: Callable[[NDArray[np.float64]], float], p: NDArray[np.float64]
) -> NDArray[np.float64]:
    """Central-difference gradient with a relative step of 1e-6."""
    g = np.zeros_like(p)
    for k in range(p.size):
        h = 1e-6 * max(abs(p[k]), 1e-8)
        dp = np.zeros_like(p)
        dp[k] = h
        g[k] = (f(p + dp) - f(p - dp)) / (2 * h)
    return g


def propagate(
    cov: NDArray[np.float64] | None, idx: list[int], grad: NDArray[np.float64]
) -> float | None:
    """σ_f = √(gᵀ Σ g) for a function of the parameters ``idx``."""
    if cov is None:
        return None
    sub = cov[np.ix_(idx, idx)]
    v = float(grad @ sub @ grad)
    return math.sqrt(v) if v >= 0 and math.isfinite(v) else None
