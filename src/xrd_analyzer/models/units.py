"""Units, angle conventions and X-ray wavelength presets.

Angle convention used everywhere in this package:

* ``two_theta_deg`` — the diffraction angle 2θ in degrees (what the instrument scans).
* ``theta`` — the Bragg angle θ = (2θ)/2. Only ever derived internally via
  :func:`bragg_theta_rad`; never accepted from users without explicit confirmation.
* FWHM values are always widths **in 2θ**; ``fwhm_deg_to_rad`` converts them to radians
  for the Scherrer / Williamson–Hall equations (β in rad, measured in 2θ).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray

ANGSTROM_PER_NM = 10.0


@dataclass(frozen=True)
class WavelengthPreset:
    key: str
    label: str
    wavelength_A: float
    note: str


# International Tables for Crystallography Vol. C (2006), Table 4.2.2.1, based on
# Hölzer et al., Phys. Rev. A 56, 4554 (1997). Users must confirm the value that matches
# their instrument's calibration; any custom wavelength can be entered instead.
CU_KA1_A = 1.5405929
CU_KA2_A = 1.5444274
# Intensity-weighted mean (2·Kα1 + Kα2)/3 — only appropriate for unresolved doublets.
CU_KA_WEIGHTED_A = (2.0 * CU_KA1_A + CU_KA2_A) / 3.0
DEFAULT_KA2_RATIO = 0.5

WAVELENGTH_PRESETS: tuple[WavelengthPreset, ...] = (
    WavelengthPreset("cu_ka1", "Cu Kα1", CU_KA1_A, "ITC Vol. C Table 4.2.2.1"),
    WavelengthPreset(
        "cu_ka_avg",
        "Cu Kα (weighted 2:1 average)",
        CU_KA_WEIGHTED_A,
        "only for unresolved Kα1/Kα2 doublets without Kα2 handling",
    ),
)

# CODATA 2018 Si lattice parameter at 22.5 °C (used only by synthetic examples/tests).
SI_LATTICE_A = 5.431020511


def preset(key: str) -> WavelengthPreset:
    for p in WAVELENGTH_PRESETS:
        if p.key == key:
            return p
    raise KeyError(f"unknown wavelength preset {key!r}")


def validate_wavelength(wavelength_A: float | None) -> float:
    """Return a validated wavelength in Å or raise ``ValueError``.

    Laboratory/synchrotron XRD wavelengths lie roughly in 0.1–3 Å; values outside
    0.05–5 Å almost certainly indicate a unit mistake (e.g. nm or pm entered as Å).
    """
    if wavelength_A is None:
        raise ValueError(
            "X-ray wavelength is not set. Select a preset (e.g. Cu Kα1 1.5405929 Å) "
            "or enter the calibrated wavelength of your instrument in Å."
        )
    w = float(wavelength_A)
    if not math.isfinite(w) or w <= 0:
        raise ValueError(f"wavelength must be a positive finite number in Å, got {wavelength_A!r}")
    if not 0.05 <= w <= 5.0:
        raise ValueError(
            f"wavelength {w} Å is outside 0.05–5 Å; check units (Å expected, not nm or pm)"
        )
    return w


def bragg_theta_rad(two_theta_deg: ArrayLike) -> NDArray[np.float64]:
    """θ in radians from 2θ in degrees: θ = radians(2θ) / 2."""
    return np.deg2rad(np.asarray(two_theta_deg, dtype=float)) / 2.0


def fwhm_deg_to_rad(fwhm_two_theta_deg: ArrayLike) -> NDArray[np.float64]:
    """Convert a peak width measured in 2θ degrees to radians (still a 2θ width)."""
    return np.deg2rad(np.asarray(fwhm_two_theta_deg, dtype=float))


def angstrom_to_nm(value_A: float) -> float:
    return value_A / ANGSTROM_PER_NM
