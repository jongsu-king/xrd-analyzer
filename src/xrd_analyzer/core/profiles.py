"""Peak profile functions. All widths are FWHM in degrees 2θ."""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import NDArray
from scipy.special import voigt_profile

FloatArray = NDArray[np.float64]
LN2 = math.log(2.0)
_G_AREA = math.sqrt(math.pi / (4.0 * LN2))  # ∫ exp(-4ln2 u²) du for unit FWHM
_L_AREA = math.pi / 2.0  # ∫ 1/(1+4u²) du for unit FWHM


def pseudo_voigt(
    x: FloatArray, center: float, height: float, fwhm: float, eta: float
) -> FloatArray:
    """Height-normalised pseudo-Voigt: H·[η·L + (1−η)·G], both components share the FWHM."""
    u = (x - center) / fwhm
    lor = 1.0 / (1.0 + 4.0 * u * u)
    gau = np.exp(-4.0 * LN2 * u * u)
    return np.asarray(height * (eta * lor + (1.0 - eta) * gau), dtype=float)


def pseudo_voigt_area(height: float, fwhm: float, eta: float) -> float:
    return height * fwhm * (eta * _L_AREA + (1.0 - eta) * _G_AREA)


def pseudo_voigt_area_grad(height: float, fwhm: float, eta: float) -> NDArray[np.float64]:
    """∂area/∂(height, fwhm, eta)."""
    a = pseudo_voigt_area(height, fwhm, eta)
    return np.array(
        [
            a / height if height else fwhm * (eta * _L_AREA + (1 - eta) * _G_AREA),
            a / fwhm if fwhm else 0.0,
            height * fwhm * (_L_AREA - _G_AREA),
        ]
    )


def voigt(x: FloatArray, center: float, area: float, sigma: float, gamma: float) -> FloatArray:
    """Area-normalised Voigt (Gaussian σ, Lorentzian HWHM γ), via scipy.special.voigt_profile."""
    return np.asarray(area * voigt_profile(x - center, sigma, gamma), dtype=float)


def voigt_fwhm(sigma: float, gamma: float) -> float:
    """Olivero & Longbothum (1977) approximation, accurate to ≈0.02 %."""
    f_g = 2.0 * sigma * math.sqrt(2.0 * LN2)
    f_l = 2.0 * gamma
    return 0.5346 * f_l + math.sqrt(0.2166 * f_l * f_l + f_g * f_g)


def voigt_height(area: float, sigma: float, gamma: float) -> float:
    return float(area * voigt_profile(0.0, sigma, gamma))


def voigt_params_from_fwhm(fwhm: float, height: float) -> tuple[float, float, float]:
    """Starting (area, σ, γ) for a Voigt with equal Gaussian/Lorentzian FWHM."""
    f = fwhm / 1.6376  # 0.5346 + √1.2166 ≈ 1.6376 when f_L = f_G
    sigma = f / (2.0 * math.sqrt(2.0 * LN2))
    gamma = f / 2.0
    peak = float(voigt_profile(0.0, sigma, gamma))
    return height / peak, sigma, gamma


def kalpha2_position(center_deg: float, lambda1_A: float, lambda2_A: float) -> float:
    """2θ of the Kα2 line belonging to a Kα1 reflection at ``center_deg``."""
    s = math.sin(math.radians(center_deg) / 2.0) * lambda2_A / lambda1_A
    if s >= 1.0:
        return math.nan
    return math.degrees(2.0 * math.asin(s))
