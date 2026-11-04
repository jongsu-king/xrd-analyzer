"""Internal, immutable representation of one 1D XRD pattern."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

import numpy as np
from numpy.typing import NDArray

# Verification status of a file format / parser (see README "Format support").
VERIFIED_NATIVE = "VERIFIED_NATIVE"  # verified against real instrument files
SPEC_DOCUMENTED = "SPEC_DOCUMENTED"  # implemented from a published spec, no real sample yet
SYNTHETIC_ONLY = "SYNTHETIC_ONLY"  # tested only with synthetic fixtures
UNSUPPORTED = "UNSUPPORTED"

# What each status means for the person reading a report. The status describes the reader,
# not the data: real measurements read by a SYNTHETIC_ONLY reader are still real.
FORMAT_STATUS_TEXT = {
    VERIFIED_NATIVE: "reader checked against real instrument files",
    SPEC_DOCUMENTED: "reader follows a published specification, no real file checked",
    SYNTHETIC_ONLY: "reader tested with synthetic files only; check the import against your data",
    UNSUPPORTED: "format not supported",
}

# Intensity units meaning raw detector counts (Poisson statistics apply).
COUNT_UNITS = frozenset({"counts", "count", "cts"})


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_arrays(*arrays: NDArray[Any]) -> str:
    h = hashlib.sha256()
    for a in arrays:
        h.update(np.ascontiguousarray(a, dtype="<f8").tobytes())
    return h.hexdigest()


def _frozen_copy(a: Any) -> NDArray[np.float64]:
    arr = np.array(a, dtype=float, copy=True)
    arr.setflags(write=False)
    return arr


@dataclass(frozen=True)
class XRDPattern:
    """A raw 1D diffraction pattern. Arrays are read-only copies of the parsed data.

    ``two_theta_deg`` is always the 2θ axis in degrees, in the order read from the file
    (sorting/duplicate handling happens in :mod:`xrd_analyzer.core.processing`, never
    on the raw object).
    """

    name: str
    two_theta_deg: NDArray[np.float64]
    intensity: NDArray[np.float64]
    intensity_unit: str = "counts"
    metadata: Mapping[str, str] = field(default_factory=dict)
    source_path: str | None = None
    sha256: str | None = None
    parser: str = "unknown"
    format_status: str = SYNTHETIC_ONLY
    synthetic: bool = False
    quality_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.intensity_unit, str) or not isinstance(self.metadata, Mapping):
            raise ValueError("intensity_unit must be text and metadata an object")
        x = _frozen_copy(self.two_theta_deg)
        y = _frozen_copy(self.intensity)
        if x.ndim != 1 or y.ndim != 1:
            raise ValueError("2θ and intensity must be 1D arrays")
        if x.shape != y.shape:
            raise ValueError(f"2θ ({x.size}) and intensity ({y.size}) lengths differ")
        object.__setattr__(self, "two_theta_deg", x)
        object.__setattr__(self, "intensity", y)
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        object.__setattr__(self, "quality_flags", tuple(self.quality_flags))
        if self.sha256 is None:
            object.__setattr__(self, "sha256", sha256_arrays(x, y))

    @property
    def n_points(self) -> int:
        return int(self.two_theta_deg.size)

    @property
    def counts_like(self) -> bool:
        """True if intensities are raw counts (Poisson weighting is then meaningful)."""
        return self.intensity_unit.strip().lower() in COUNT_UNITS

    def summary(self) -> dict[str, Any]:
        x = self.two_theta_deg
        return {
            "name": self.name,
            "n_points": self.n_points,
            "two_theta_min_deg": float(np.min(x)) if x.size else None,
            "two_theta_max_deg": float(np.max(x)) if x.size else None,
            "intensity_unit": self.intensity_unit,
            "parser": self.parser,
            "format_status": self.format_status,
            "synthetic": self.synthetic,
            "source_path": self.source_path,
            "sha256": self.sha256,
            "array_sha256": sha256_arrays(self.two_theta_deg, self.intensity),
            "quality_flags": list(self.quality_flags),
            "metadata": dict(self.metadata),
        }
