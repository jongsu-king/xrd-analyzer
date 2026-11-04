"""Non-linear peak fitting (pseudo-Voigt or Voigt) with covariance and residual statistics.

Overlapping fit windows are merged and fitted jointly. Uncertainties come from the
Jacobian at the optimum (cov = (JᵀJ)⁻¹). With Poisson weights (raw counts) the covariance
is scaled by max(1, χ²_red); without weights it is scaled by χ²_red (σ estimated from the
residuals). Parameters at a bound or an ill-conditioned Jacobian are flagged, not hidden.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import least_squares

from . import profiles
from .peaks import PeakCandidate
from .uncertainty import covariance_from_jacobian, num_grad, propagate, std_from_cov

FloatArray = NDArray[np.float64]

# Below this reduced χ² the assumed σ is too large, which is what count rates labelled as
# counts look like. Small windows scatter widely, so it needs enough degrees of freedom.
LOW_CHI2_RED = 0.5
LOW_CHI2_MIN_DOF = 20


class AnalysisCancelled(RuntimeError):
    pass


@dataclass
class PeakFit:
    peak_id: int
    source: str
    profile: str
    group_id: int
    center_deg: float
    center_err: float | None
    height: float
    height_err: float | None
    fwhm_deg: float
    fwhm_err: float | None
    area: float
    area_err: float | None
    eta: float | None = None
    eta_err: float | None = None
    sigma_deg: float | None = None
    sigma_err: float | None = None
    gamma_deg: float | None = None
    gamma_err: float | None = None
    doublet: bool = False
    flags: list[str] = field(default_factory=list)
    # √Δχ²: how much worse the fit gets without this peak, in σ, over the whole fit window
    significance: float | None = None

    @property
    def ok(self) -> bool:
        """Finite parameters were obtained (the fit ran)."""
        return math.isfinite(self.center_deg) and "fit_skipped" not in self.flags

    @property
    def eligible(self) -> bool:
        """Usable for derived quantities: converged and identifiable."""
        return self.ok and not ({"not_converged", "parameters_not_identifiable"} & set(self.flags))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FitGroup:
    group_id: int
    peak_ids: list[int]
    lo_deg: float
    hi_deg: float
    n_points: int
    n_params: int
    dof: int
    chi2: float
    chi2_red: float
    r2: float
    condition_number: float
    converged: bool
    message: str
    weighted: bool
    background: list[float]
    flags: list[str] = field(default_factory=list)
    x: FloatArray | None = None
    y: FloatArray | None = None
    y_fit: FloatArray | None = None

    def to_dict(self, include_arrays: bool = False) -> dict[str, Any]:
        d = {k: v for k, v in asdict(self).items() if k not in {"x", "y", "y_fit"}}
        if include_arrays and self.x is not None:
            d["x"] = self.x.tolist()
            d["y"] = self.y.tolist() if self.y is not None else None
            d["y_fit"] = self.y_fit.tolist() if self.y_fit is not None else None
        return d


@dataclass
class FitResult:
    peaks: list[PeakFit]
    groups: list[FitGroup]
    warnings: list[str] = field(default_factory=list)
    doublet: tuple[float, float, float] | None = None

    def model(self, x: FloatArray) -> FloatArray:
        """Sum of all fitted profiles (incl. Kα2 partners, without local backgrounds)."""
        xx = np.asarray(x, dtype=float)
        out = np.zeros_like(xx)
        for p in self.peaks:
            if p.ok:
                out += peak_curve(xx, p, self.doublet)
        return out


def peak_curve(
    x: FloatArray, p: PeakFit, doublet: tuple[float, float, float] | None = None
) -> FloatArray:
    if p.profile == "voigt":
        assert p.sigma_deg is not None and p.gamma_deg is not None
        a, w, e = p.area, p.sigma_deg, p.gamma_deg
    else:
        assert p.eta is not None
        a, w, e = p.height, p.fwhm_deg, p.eta
    out = _profile(x, p.profile, p.center_deg, a, w, e)
    if doublet is not None and p.doublet:
        c2 = profiles.kalpha2_position(p.center_deg, doublet[0], doublet[1])
        if math.isfinite(c2):
            out = out + doublet[2] * _profile(x, p.profile, c2, a, w, e)
    return out


def _groups(
    cands: list[PeakCandidate], factor: float, xmin: float, xmax: float
) -> list[tuple[float, float, list[int]]]:
    spans = sorted(
        (
            max(xmin, c.position_deg - factor * c.fwhm_est_deg),
            min(xmax, c.position_deg + factor * c.fwhm_est_deg),
            i,
        )
        for i, c in enumerate(cands)
    )
    merged: list[tuple[float, float, list[int]]] = []
    for lo, hi, i in spans:
        if merged and lo <= merged[-1][1]:
            plo, phi, ids = merged[-1]
            merged[-1] = (plo, max(phi, hi), [*ids, i])
        else:
            merged.append((lo, hi, [i]))
    return merged


def fit_peaks(
    x: FloatArray,
    y: FloatArray,
    candidates: list[PeakCandidate],
    profile: str = "pseudo_voigt",
    window_fwhm_factor: float = 4.0,
    local_background: str = "linear",
    sigma: FloatArray | None = None,
    doublet: tuple[float, float, float] | None = None,
    max_nfev: int = 4000,
    cancel: Callable[[], bool] | None = None,
) -> FitResult:
    """Fit ``candidates`` on net intensity ``y``.

    ``sigma``: per-point standard deviations (Poisson) or None for unweighted fitting.
    ``doublet``: (λ1, λ2, ratio) to fit each reflection as a Kα1/Kα2 pair; the reported
    centre is then the Kα1 position.
    """
    if profile not in {"pseudo_voigt", "voigt"}:
        raise ValueError(f"unknown profile {profile!r}")
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    step = float(np.median(np.diff(x)))
    result = FitResult([], [], doublet=doublet)
    if not candidates:
        result.warnings.append("no peaks to fit")
        return result
    nb = {"none": 0, "constant": 1, "linear": 2}[local_background]
    for gid, (lo, hi, ids) in enumerate(_groups(candidates, window_fwhm_factor, x[0], x[-1])):
        if cancel is not None and cancel():
            raise AnalysisCancelled("fit cancelled by user")
        mask = (x >= lo) & (x <= hi)
        xs, ys = x[mask], y[mask]
        ss = sigma[mask] if sigma is not None else np.ones_like(ys)
        ss = np.where(ss > 0, ss, 1.0)
        group_cands = [candidates[i] for i in ids]
        npar = 4 * len(ids) + nb
        if xs.size <= npar + 1:
            result.groups.append(
                FitGroup(
                    gid,
                    ids,
                    lo,
                    hi,
                    int(xs.size),
                    npar,
                    0,
                    math.nan,
                    math.nan,
                    math.nan,
                    math.nan,
                    False,
                    "too few points in window",
                    sigma is not None,
                    [],
                    ["too_few_points"],
                )
            )
            for i, c in zip(ids, group_cands, strict=True):
                result.peaks.append(_skipped(i, c, profile, gid))
            continue
        xm = float(xs.mean())
        p0: list[float] = []
        lb: list[float] = []
        ub: list[float] = []
        ymax = float(np.max(np.abs(ys))) or 1.0
        bg0 = float(np.percentile(ys, 10)) if nb else 0.0
        width_cap = max(hi - lo, 2 * step)
        for c in group_cands:
            w0 = min(max(c.fwhm_est_deg, 2 * step), width_cap / 2)
            h0 = max(float(np.interp(c.position_deg, xs, ys)) - bg0, 1e-6 * ymax)
            dc = max(w0, 2 * step)
            if profile == "pseudo_voigt":
                p0 += [c.position_deg, h0, w0, 0.5]
                lb += [c.position_deg - dc, 0.0, 0.5 * step, 0.0]
                ub += [c.position_deg + dc, 20 * ymax, width_cap, 1.0]
            else:
                a0, s0, g0 = profiles.voigt_params_from_fwhm(w0, h0)
                p0 += [c.position_deg, a0, s0, g0]
                lb += [c.position_deg - dc, 0.0, 0.1 * step, 0.0]
                ub += [c.position_deg + dc, 20 * ymax * width_cap, width_cap, width_cap]
        if nb >= 1:
            p0.append(bg0)
            lb.append(-np.inf)
            ub.append(np.inf)
        if nb == 2:
            p0.append(0.0)
            lb.append(-np.inf)
            ub.append(np.inf)
        lb_a, ub_a = np.array(lb), np.array(ub)
        span = np.where(np.isfinite(ub_a - lb_a), ub_a - lb_a, 1.0)
        p0_a = np.clip(np.array(p0), lb_a + 1e-9 * span, ub_a - 1e-9 * span)
        npk = len(ids)

        def model(
            p: NDArray[np.float64], xx: FloatArray = xs, npk: int = npk, xm: float = xm
        ) -> FloatArray:
            out = np.zeros_like(xx)
            for k in range(npk):
                c, a, w, e = p[4 * k : 4 * k + 4]
                out += _profile(xx, profile, c, a, w, e)
                if doublet is not None:
                    c2 = profiles.kalpha2_position(c, doublet[0], doublet[1])
                    if math.isfinite(c2):
                        out += doublet[2] * _profile(xx, profile, c2, a, w, e)
            if nb >= 1:
                out += p[4 * npk]
            if nb == 2:
                out += p[4 * npk + 1] * (xx - xm)
            return out

        def resid(p: NDArray[np.float64], ys: FloatArray = ys, ss: FloatArray = ss) -> FloatArray:
            if cancel is not None and cancel():  # responsive cancel inside the solver
                raise AnalysisCancelled("fit cancelled by user")
            return (model(p) - ys) / ss

        try:
            res = least_squares(
                resid, p0_a, bounds=(lb_a, ub_a), method="trf", x_scale="jac", max_nfev=max_nfev
            )
        except (ValueError, np.linalg.LinAlgError) as exc:
            result.groups.append(
                FitGroup(
                    gid,
                    ids,
                    lo,
                    hi,
                    int(xs.size),
                    npar,
                    0,
                    math.nan,
                    math.nan,
                    math.nan,
                    math.nan,
                    False,
                    str(exc),
                    sigma is not None,
                    [],
                    ["fit_failed"],
                )
            )
            for i, c in zip(ids, group_cands, strict=True):
                result.peaks.append(_skipped(i, c, profile, gid))
            continue
        p = res.x
        dof = int(xs.size - npar)
        chi2 = float(np.sum(res.fun**2))
        chi2_red = chi2 / dof if dof > 0 else math.nan
        yfit = model(p)
        ss_tot = float(np.sum((ys - ys.mean()) ** 2))
        r2 = 1.0 - float(np.sum((ys - yfit) ** 2)) / ss_tot if ss_tot > 0 else math.nan
        cov, cond, gflags = covariance_from_jacobian(res.jac, chi2_red, sigma is not None)
        if not res.success:
            gflags.append("not_converged")
        if sigma is not None and math.isfinite(chi2_red) and chi2_red > 3:
            gflags.append("chi2_red>3 (model or weighting inadequate)")
        if sigma is not None and dof >= LOW_CHI2_MIN_DOF and chi2_red < LOW_CHI2_RED:
            gflags.append("chi2_red<0.5 (σ too large: are these really raw counts?)")
        at_bound = res.active_mask != 0
        bg = [float(v) for v in p[4 * npk :]]
        result.groups.append(
            FitGroup(
                gid,
                ids,
                lo,
                hi,
                int(xs.size),
                npar,
                dof,
                chi2,
                chi2_red,
                r2,
                cond,
                bool(res.success),
                str(res.message),
                sigma is not None,
                bg,
                gflags,
                xs,
                ys,
                yfit,
            )
        )
        for k, (i, c) in enumerate(zip(ids, group_cands, strict=True)):
            b = 4 * k
            pk = p[b : b + 4]
            pf = _peak_from_params(i, c, profile, gid, pk, cov, b, step)
            pf.doublet = doublet is not None
            if not res.success:
                pf.flags.append("not_converged")
            if at_bound[b]:
                pf.flags.append("center_at_bound")
            if at_bound[b + 2]:
                pf.flags.append("width_at_bound")
            if at_bound[b + 1]:
                pf.flags.append("amplitude_at_bound")
            # active_mask is unreliable for trf, whose iterates stay strictly inside the bounds
            if at_bound[b + 3] or (profile == "pseudo_voigt" and not 1e-3 < pk[3] < 0.999):
                pf.flags.append("shape_at_bound")  # η or the Lorentzian width γ
            want = window_fwhm_factor * c.fwhm_est_deg
            if c.position_deg - want < x[0] or c.position_deg + want > x[-1]:
                pf.flags.append("window_truncated_at_scan_edge")
            if "ill_conditioned" in gflags or cov is None:
                pf.flags.append("parameters_not_identifiable")
            result.peaks.append(pf)
    result.peaks.sort(key=lambda q: q.center_deg if math.isfinite(q.center_deg) else 1e9)
    return result


def _profile(xx: FloatArray, profile: str, c: float, a: float, w: float, e: float) -> FloatArray:
    if profile == "pseudo_voigt":
        return profiles.pseudo_voigt(xx, c, a, w, e)
    return profiles.voigt(xx, c, a, w, e)


def _skipped(i: int, c: PeakCandidate, profile: str, gid: int) -> PeakFit:
    nan = math.nan
    return PeakFit(
        i, c.source, profile, gid, nan, None, nan, None, nan, None, nan, None, flags=["fit_skipped"]
    )


def _peak_from_params(
    i: int,
    c: PeakCandidate,
    profile: str,
    gid: int,
    pk: NDArray[np.float64],
    cov: NDArray[np.float64] | None,
    b: int,
    step: float,
) -> PeakFit:
    flags: list[str] = []
    if profile == "pseudo_voigt":
        center, height, fwhm, eta = (float(v) for v in pk)
        area = profiles.pseudo_voigt_area(height, fwhm, eta)
        area_err = propagate(
            cov, [b + 1, b + 2, b + 3], profiles.pseudo_voigt_area_grad(height, fwhm, eta)
        )
        pf = PeakFit(
            i,
            c.source,
            profile,
            gid,
            center,
            std_from_cov(cov, b),
            height,
            std_from_cov(cov, b + 1),
            fwhm,
            std_from_cov(cov, b + 2),
            area,
            area_err,
            eta=eta,
            eta_err=std_from_cov(cov, b + 3),
        )
    else:
        center, area, sg, gm = (float(v) for v in pk)
        fwhm = profiles.voigt_fwhm(sg, gm)
        fwhm_err = propagate(
            cov,
            [b + 2, b + 3],
            num_grad(lambda q: profiles.voigt_fwhm(q[0], q[1]), np.array([sg, gm])),
        )
        height = profiles.voigt_height(area, sg, gm)
        height_err = propagate(
            cov,
            [b + 1, b + 2, b + 3],
            num_grad(lambda q: profiles.voigt_height(q[0], q[1], q[2]), np.array([area, sg, gm])),
        )
        pf = PeakFit(
            i,
            c.source,
            profile,
            gid,
            center,
            std_from_cov(cov, b),
            height,
            height_err,
            fwhm,
            fwhm_err,
            area,
            std_from_cov(cov, b + 1),
            sigma_deg=sg,
            sigma_err=std_from_cov(cov, b + 2),
            gamma_deg=gm,
            gamma_err=std_from_cov(cov, b + 3),
        )
    if fwhm < 3 * step:
        flags.append("undersampled (FWHM < 3 steps)")
    if pf.height_err is not None and pf.height < 3 * pf.height_err:
        flags.append("low_significance (height < 3σ)")
    pf.flags.extend(flags)
    return pf
