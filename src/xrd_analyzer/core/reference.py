"""Position-based comparison with user-supplied or lattice-computed reference patterns.

No crystallographic database is bundled. References are either (a) CSV files the user is
licensed to use, or (b) reflection positions computed from a cubic lattice parameter. The
comparison is a *candidate* ranking by peak positions only; it is not a phase
identification proof and never yields phase fractions.
"""

from __future__ import annotations

import csv
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .crystallography import cubic_reflections, d_spacing, two_theta_from_d

CAVEATS = [
    "position-only comparison; not a proof of phase identity",
    "relative intensities are affected by texture/preferred orientation and are not used "
    "for quantification",
    "no phase fractions are derived from peak intensities",
]


@dataclass
class ReferencePeak:
    d_A: float
    rel_intensity: float | None = None
    hkl: str = ""


@dataclass
class ReferencePattern:
    name: str
    source: str
    license_note: str
    peaks: list[ReferencePeak]
    computed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def reference_from_cubic(
    name: str, a_A: float, centering: str, wavelength_A: float, two_theta_max: float = 150.0
) -> ReferencePattern:
    refl = cubic_reflections(a_A, centering, wavelength_A, two_theta_max=two_theta_max)
    return ReferencePattern(
        name=name,
        source=f"computed from cubic lattice a = {a_A} Å ({centering}); positions only",
        license_note="computed by this program (no database content)",
        peaks=[ReferencePeak(r.d_A, None, r.hkl) for r in refl],
        computed=True,
    )


def load_reference_csv(path: str | Path) -> ReferencePattern:
    """CSV with columns ``d_A`` *or* ``two_theta_deg`` (+ ``# wavelength_A:`` comment),
    optional ``rel_intensity`` and ``hkl``. Comment lines ``# name:``, ``# source:`` and
    ``# license:`` are recorded; a missing source/license is flagged in the name."""
    p = Path(path)
    meta: dict[str, str] = {}
    rows: list[str] = []
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            body = s.lstrip("# ")
            if ":" in body:
                k, v = body.split(":", 1)
                meta[k.strip().lower()] = v.strip()
            continue
        rows.append(s)
    if not rows:
        raise ValueError(f"{p.name}: reference file has no data")
    reader = csv.DictReader(rows)
    fields = {f.strip().lower(): f for f in (reader.fieldnames or [])}
    if "d_a" not in fields and "two_theta_deg" not in fields:
        raise ValueError(f"{p.name}: need a 'd_A' or 'two_theta_deg' column")
    ref_wl: float | None = None
    if "d_a" not in fields:
        if "wavelength_a" not in meta:
            raise ValueError(f"{p.name}: 2θ reference needs a '# wavelength_A: …' comment line")
        ref_wl = float(meta["wavelength_a"])
    peaks: list[ReferencePeak] = []
    for i, row in enumerate(reader, 2):
        try:
            if "d_a" in fields:
                d = float(row[fields["d_a"]])
            else:
                assert ref_wl is not None
                d = d_spacing(float(row[fields["two_theta_deg"]]), ref_wl)
            ri = row.get(fields.get("rel_intensity", ""), "")
            peaks.append(
                ReferencePeak(
                    d,
                    float(ri) if ri not in (None, "") else None,
                    (row.get(fields.get("hkl", ""), "") or "").strip(),
                )
            )
        except (ValueError, KeyError) as exc:
            raise ValueError(f"{p.name}: bad reference row {i}: {exc}") from exc
    if any(not (pk.d_A > 0 and math.isfinite(pk.d_A)) for pk in peaks):
        raise ValueError(f"{p.name}: d-spacings must be positive")
    name = meta.get("name", p.stem)
    source = meta.get("source", "")
    lic = meta.get("license", "")
    if not source or not lic:
        name += " [source/license not stated]"
    return ReferencePattern(name, source or "unspecified", lic or "unspecified", peaks)


@dataclass
class PeakMatch:
    hkl: str
    ref_two_theta_deg: float
    obs_two_theta_deg: float | None
    delta_deg: float | None


@dataclass
class MatchResult:
    reference: str
    n_expected: int
    n_matched: int
    fraction: float
    mean_abs_delta_deg: float | None
    median_delta_deg: float | None
    unexplained_observed: int
    confidence: str
    matches: list[PeakMatch] = field(default_factory=list)
    caveats: list[str] = field(default_factory=lambda: list(CAVEATS))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def match_reference(
    observed_deg: list[float],
    ref: ReferencePattern,
    wavelength_A: float,
    two_theta_range: tuple[float, float],
    tolerance_deg: float = 0.1,
    min_rel_intensity: float = 5.0,
) -> MatchResult:
    lo, hi = two_theta_range
    expected: list[tuple[str, float]] = []
    for pk in ref.peaks:
        if pk.rel_intensity is not None and pk.rel_intensity < min_rel_intensity:
            continue
        try:
            tt = two_theta_from_d(pk.d_A, wavelength_A)
        except ValueError:
            continue
        if lo <= tt <= hi:
            expected.append((pk.hkl, tt))
    obs = np.array(sorted(observed_deg), dtype=float)
    used: set[int] = set()
    matches: list[PeakMatch] = []
    for hkl, tt in expected:
        if obs.size:
            j = int(np.argmin(np.abs(obs - tt)))
            if abs(obs[j] - tt) <= tolerance_deg and j not in used:
                used.add(j)
                matches.append(PeakMatch(hkl, tt, float(obs[j]), float(obs[j] - tt)))
                continue
        matches.append(PeakMatch(hkl, tt, None, None))
    deltas = [m.delta_deg for m in matches if m.delta_deg is not None]
    n_exp, n_match = len(expected), len(deltas)
    frac = n_match / n_exp if n_exp else 0.0
    mad = float(np.mean(np.abs(deltas))) if deltas else None
    if n_exp == 0:
        conf = "not_applicable (no reference peaks in range)"
    elif n_exp >= 3 and frac >= 0.8 and mad is not None and mad <= tolerance_deg / 2:
        conf = "high"
    elif n_exp >= 2 and frac >= 0.5:
        conf = "medium"
    else:
        conf = "low"
    return MatchResult(
        ref.name,
        n_exp,
        n_match,
        frac,
        mad,
        float(np.median(deltas)) if deltas else None,
        int(obs.size - len(used)),
        conf,
        matches,
    )
