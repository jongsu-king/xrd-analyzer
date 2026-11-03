"""Bragg d-spacing, Scherrer size, Williamson–Hall and cubic reflection lists.

Conventions: 2θ in degrees as input; θ = (2θ)/2 is derived internally; peak widths β are
FWHM **in 2θ**, converted to radians before entering Scherrer/W–H. Every result carries
its formula, assumptions and a status; insufficient inputs give ``NOT_CALCULABLE`` with a
reason instead of a number.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

OK = "OK"
WARNING = "WARNING"
NOT_CALCULABLE = "NOT_CALCULABLE"


@dataclass
class Derived:
    name: str
    value: float | None
    uncertainty: float | None
    unit: str
    status: str
    formula: str
    assumptions: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _theta_rad(two_theta_deg: float) -> float:
    if not 0.0 < two_theta_deg < 180.0:
        raise ValueError(f"2θ must lie in (0°, 180°), got {two_theta_deg}")
    return math.radians(two_theta_deg) / 2.0


# ------------------------------------------------------------------------------- Bragg

BRAGG_FORMULA = "d = nλ / (2 sin θ), θ = (2θ)/2, n = 1"


def d_spacing(two_theta_deg: float, wavelength_A: float, order: int = 1) -> float:
    return order * wavelength_A / (2.0 * math.sin(_theta_rad(two_theta_deg)))


def two_theta_from_d(d_A: float, wavelength_A: float, order: int = 1) -> float:
    s = order * wavelength_A / (2.0 * d_A)
    if not 0 < s < 1:
        raise ValueError(f"no reflection: λ/(2d) = {s:.4f} is outside (0, 1)")
    return math.degrees(2.0 * math.asin(s))


def bragg(
    two_theta_deg: float, wavelength_A: float, sigma_two_theta_deg: float | None = None
) -> Derived:
    if not (math.isfinite(wavelength_A) and wavelength_A > 0):
        return Derived(
            "d-spacing",
            None,
            None,
            "Å",
            NOT_CALCULABLE,
            BRAGG_FORMULA,
            [],
            "wavelength must be a positive finite number",
        )
    d = d_spacing(two_theta_deg, wavelength_A)
    theta = _theta_rad(two_theta_deg)
    unc = None
    reason = ""
    if sigma_two_theta_deg is not None and math.isfinite(sigma_two_theta_deg):
        sigma_theta = math.radians(sigma_two_theta_deg) / 2.0
        unc = d * sigma_theta / math.tan(theta)  # |∂d/∂θ| = d·cot θ
    else:
        reason = "peak-position uncertainty unavailable"
    return Derived(
        "d-spacing",
        d,
        unc,
        "Å",
        OK,
        BRAGG_FORMULA,
        [
            "uncertainty from the fitted 2θ only (wavelength uncertainty not included)",
            "no zero-shift / sample-displacement correction applied",
        ],
        reason,
    )


# ------------------------------------------------------------------ instrumental width


@dataclass
class InstrumentBroadening:
    """Instrumental FWHM (degrees 2θ): constant, or Caglioti FWHM² = U tan²θ + V tanθ + W."""

    mode: str = "none"
    fwhm_deg: float | None = None
    U: float | None = None
    V: float | None = None
    W: float | None = None

    def fwhm_at(self, two_theta_deg: float) -> float | None:
        if self.mode == "constant":
            return self.fwhm_deg
        if self.mode == "caglioti" and None not in (self.U, self.V, self.W):
            assert self.U is not None and self.V is not None and self.W is not None
            t = math.tan(_theta_rad(two_theta_deg))
            v = self.U * t * t + self.V * t + self.W
            return math.sqrt(v) if v > 0 else None
        return None

    def describe(self) -> str:
        if self.mode == "constant":
            return f"constant instrumental FWHM = {self.fwhm_deg}° (2θ)"
        if self.mode == "caglioti":
            return f"Caglioti U={self.U}, V={self.V}, W={self.W} deg²"
        return "not provided"


def sample_broadening(
    beta_meas_deg: float, beta_inst_deg: float, correction: str, sigma_meas_deg: float | None
) -> tuple[float | None, float | None, str]:
    """Return (β_sample, σ, reason). β_sample is None if the correction is not valid."""
    if not math.isfinite(beta_meas_deg) or beta_meas_deg <= 0:
        return None, None, "measured FWHM is not a positive finite number"
    if not math.isfinite(beta_inst_deg) or beta_inst_deg < 0:
        return None, None, "instrumental FWHM must be a finite number ≥ 0"
    if beta_meas_deg <= beta_inst_deg:
        return (
            None,
            None,
            (
                f"measured FWHM {beta_meas_deg:.4f}° ≤ instrumental {beta_inst_deg:.4f}°: no "
                "resolvable sample broadening"
            ),
        )
    if correction == "quadratic":
        bs = math.sqrt(beta_meas_deg**2 - beta_inst_deg**2)
        sig = beta_meas_deg * sigma_meas_deg / bs if sigma_meas_deg is not None else None
    elif correction == "linear":
        bs = beta_meas_deg - beta_inst_deg
        sig = sigma_meas_deg
    else:
        raise ValueError(f"unknown broadening correction {correction!r}")
    return bs, sig, ""


_CORRECTION_TEXT = {
    "quadratic": "β_sample² = β_measured² − β_instrument² (Gaussian-profile approximation)",
    "linear": "β_sample = β_measured − β_instrument (Lorentzian-profile approximation)",
}

SCHERRER_FORMULA = "D = K·λ / (β·cos θ), β = sample FWHM in 2θ [rad], θ = (2θ)/2"


def scherrer(
    two_theta_deg: float,
    fwhm_deg: float,
    fwhm_err_deg: float | None,
    K: float | None,
    wavelength_A: float,
    instrument: InstrumentBroadening,
    correction: str = "quadratic",
) -> Derived:
    base = [
        "β is the FWHM in 2θ converted from degrees to radians",
        "all sample broadening attributed to crystallite size (microstrain ignored)",
        "result is a volume-weighted apparent size; K depends on crystallite shape",
        "instrumental-broadening uncertainty not included",
    ]

    def nc(reason: str) -> Derived:
        return Derived(
            "Scherrer crystallite size",
            None,
            None,
            "nm",
            NOT_CALCULABLE,
            SCHERRER_FORMULA,
            base,
            reason,
        )

    if K is None:
        return nc("shape factor K not provided")
    if not math.isfinite(K) or K <= 0:
        return nc("shape factor K must be a positive finite number")
    if not math.isfinite(wavelength_A) or wavelength_A <= 0:
        return nc("wavelength must be a positive finite number")
    if instrument.mode == "none":
        return nc(
            "instrumental broadening not provided — a definitive crystallite size "
            "cannot be reported"
        )
    beta_i = instrument.fwhm_at(two_theta_deg)
    if beta_i is None:
        return nc("instrumental FWHM undefined at this angle (check Caglioti U, V, W)")
    bs, sig, reason = sample_broadening(fwhm_deg, beta_i, correction, fwhm_err_deg)
    if bs is None:
        return nc(reason)
    theta = _theta_rad(two_theta_deg)
    beta_rad = math.radians(bs)
    d_nm = K * wavelength_A / (beta_rad * math.cos(theta)) / 10.0
    unc = d_nm * sig / bs if sig is not None else None
    assumptions = [_CORRECTION_TEXT[correction], f"K = {K}", *base]
    status = OK
    notes: list[str] = []
    if beta_i == 0:
        status = WARNING
        notes.append("instrumental FWHM entered as 0: value is only a lower bound")
    if fwhm_deg < 1.2 * beta_i:
        status = WARNING
        notes.append("measured width < 1.2× instrumental width: large systematic uncertainty")
    if d_nm > 150:
        status = WARNING
        notes.append("size > ~150 nm is beyond the reliable range of the Scherrer method")
    return Derived(
        "Scherrer crystallite size",
        d_nm,
        unc,
        "nm",
        status,
        SCHERRER_FORMULA,
        assumptions,
        "; ".join(notes) if notes else ("" if unc is not None else "FWHM uncertainty unavailable"),
    )


# ---------------------------------------------------------------------- Williamson–Hall

WH_FORMULA = "β·cos θ = K·λ/D + 4ε·sin θ (uniform deformation model)"


@dataclass
class WHResult:
    status: str
    reason: str
    n_peaks: int
    size: Derived | None = None
    strain: Derived | None = None
    r2: float | None = None
    chi2_red: float | None = None
    x: list[float] = field(default_factory=list)  # 4 sin θ
    y: list[float] = field(default_factory=list)  # β cos θ [rad]
    y_err: list[float | None] = field(default_factory=list)
    two_theta_deg: list[float] = field(default_factory=list)
    intercept: float | None = None
    slope: float | None = None
    excluded: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


def williamson_hall(
    peaks: list[tuple[float, float, float | None]],
    K: float | None,
    wavelength_A: float,
    instrument: InstrumentBroadening,
    correction: str = "quadratic",
    min_peaks: int = 3,
) -> WHResult:
    """``peaks``: list of (2θ deg, FWHM deg, σ_FWHM deg or None) — all from ONE phase."""
    if not (
        isinstance(wavelength_A, int | float) and math.isfinite(wavelength_A) and wavelength_A > 0
    ):
        return WHResult(NOT_CALCULABLE, "wavelength must be a positive finite number", 0)
    assumptions = [
        WH_FORMULA,
        _CORRECTION_TEXT.get(correction, correction),
        "isotropic size and strain; all peaks belong to the same phase",
        "ε is an apparent microstrain derived from FWHM (not integral breadth)",
    ]
    if K is None or not math.isfinite(K) or K <= 0:
        return WHResult(
            NOT_CALCULABLE,
            "shape factor K not provided or not a positive finite number",
            0,
            assumptions=assumptions,
        )
    if instrument.mode == "none":
        return WHResult(
            NOT_CALCULABLE, "instrumental broadening not provided", 0, assumptions=assumptions
        )
    xs: list[float] = []
    ys: list[float] = []
    es: list[float | None] = []
    tts: list[float] = []
    excluded: list[str] = []
    for tt, fw, fe in peaks:
        bi = instrument.fwhm_at(tt)
        if bi is None:
            excluded.append(f"{tt:.3f}°: instrumental FWHM undefined")
            continue
        bs, sig, reason = sample_broadening(fw, bi, correction, fe)
        if bs is None:
            excluded.append(f"{tt:.3f}°: {reason}")
            continue
        th = _theta_rad(tt)
        xs.append(4.0 * math.sin(th))
        ys.append(math.radians(bs) * math.cos(th))
        es.append(math.radians(sig) * math.cos(th) if sig is not None else None)
        tts.append(tt)
    n = len(xs)
    res = WHResult(
        NOT_CALCULABLE,
        "",
        n,
        x=xs,
        y=ys,
        y_err=es,
        two_theta_deg=tts,
        excluded=excluded,
        assumptions=assumptions,
    )
    if n < min_peaks:
        res.reason = f"only {n} usable peak(s); Williamson–Hall needs ≥ {min_peaks}"
        return res
    xa, ya = np.array(xs), np.array(ys)
    if np.ptp(xa) < 0.2:
        res.reason = "peaks span too small a 4 sin θ range (< 0.2) to separate size and strain"
        return res
    weighted = all(e is not None and e > 0 for e in es)
    w = np.array([1.0 / e**2 for e in es]) if weighted else np.ones(n)  # type: ignore[operator]
    a = np.vstack([np.ones(n), xa]).T
    ata = a.T @ (a * w[:, None])
    try:
        cov = np.linalg.inv(ata)
    except np.linalg.LinAlgError:
        res.reason = "singular regression matrix"
        return res
    beta = cov @ (a.T @ (w * ya))
    resid = ya - a @ beta
    dof = n - 2
    chi2_red = float(np.sum(w * resid**2) / dof) if dof > 0 else math.nan
    if weighted:
        cov = cov * max(1.0, chi2_red if math.isfinite(chi2_red) else 1.0)
    else:
        cov = cov * (chi2_red if math.isfinite(chi2_red) else 0.0)
    c, m = float(beta[0]), float(beta[1])
    sc, sm = float(math.sqrt(max(cov[0, 0], 0))), float(math.sqrt(max(cov[1, 1], 0)))
    ss_tot = float(np.sum((ya - ya.mean()) ** 2))
    res.r2 = 1.0 - float(np.sum(resid**2)) / ss_tot if ss_tot > 0 else None
    res.chi2_red = chi2_red
    res.intercept, res.slope = c, m
    notes: list[str] = []
    if dof == 1:
        notes.append("only 1 degree of freedom: uncertainties are poorly determined")
    strain_status = OK if m >= 0 else WARNING
    if m < 0:
        notes.append(
            "negative slope is inconsistent with the uniform-strain model "
            "(anisotropic broadening, mixed phases or wrong instrument correction?)"
        )
    res.strain = Derived(
        "W–H microstrain ε",
        m,
        sm if dof > 0 else None,
        "dimensionless",
        strain_status,
        WH_FORMULA,
        assumptions,
    )
    if c <= 0:
        res.size = Derived(
            "W–H crystallite size",
            None,
            None,
            "nm",
            NOT_CALCULABLE,
            WH_FORMULA,
            assumptions,
            "non-physical intercept ≤ 0 (size broadening not resolved)",
        )
    else:
        d_nm = K * wavelength_A / c / 10.0
        size_status = OK
        if c - 2 * sc <= 0:
            size_status = WARNING
            notes.append("intercept within 2σ of zero: size poorly determined")
        res.size = Derived(
            "W–H crystallite size",
            d_nm,
            d_nm * sc / c if dof > 0 else None,
            "nm",
            size_status,
            WH_FORMULA,
            assumptions,
        )
    res.status = WARNING if notes else OK
    res.reason = "; ".join(notes)
    return res


# ------------------------------------------------------------------- cubic reflections


@dataclass(frozen=True)
class Reflection:
    hkl: str
    n_sq: int
    d_A: float
    two_theta_deg: float
    multiplicity: int


def _allowed(h: int, k: int, l: int, centering: str) -> bool:  # noqa: E741
    if centering == "P":
        return True
    if centering == "I":
        return (h + k + l) % 2 == 0
    parity = {h % 2, k % 2, l % 2}
    all_same = len(parity) == 1
    if centering == "F":
        return all_same
    if centering == "diamond":
        if not all_same:
            return False
        if h % 2 == 1:
            return True
        return (h + k + l) % 4 == 0
    raise ValueError(f"unknown centering {centering!r} (P, I, F, diamond)")


def cubic_reflections(
    a_A: float,
    centering: str,
    wavelength_A: float,
    two_theta_min: float = 0.0,
    two_theta_max: float = 180.0,
    hkl_max: int = 10,
) -> list[Reflection]:
    """Allowed reflections of a cubic lattice (positions only; no structure factors)."""
    by_n: dict[int, list[tuple[int, int, int]]] = {}
    for h, k, l in itertools.product(range(hkl_max + 1), repeat=3):  # noqa: E741
        if (h, k, l) == (0, 0, 0) or not h >= k >= l:
            continue
        if not _allowed(h, k, l, centering):
            continue
        by_n.setdefault(h * h + k * k + l * l, []).append((h, k, l))
    out: list[Reflection] = []
    for n_sq in sorted(by_n):
        d = a_A / math.sqrt(n_sq)
        s = wavelength_A / (2 * d)
        if s >= 1:
            break
        tt = math.degrees(2 * math.asin(s))
        if not two_theta_min <= tt <= two_theta_max:
            continue
        fams = by_n[n_sq]
        mult = sum(len(_signed_perms(f)) for f in fams)
        label = "/".join("".join(str(i) for i in f) for f in sorted(fams, reverse=True))
        out.append(Reflection(label, n_sq, d, tt, mult))
    return out


def _signed_perms(hkl: tuple[int, int, int]) -> set[tuple[int, ...]]:
    out: set[tuple[int, ...]] = set()
    for perm in itertools.permutations(hkl):
        for signs in itertools.product((1, -1), repeat=3):
            out.add(tuple(p * s for p, s in zip(perm, signs, strict=True)))
    return out
