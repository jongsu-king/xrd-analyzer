"""End-to-end analysis: raw pattern + recipe → AnalysisResult (UI-independent).

Order: axis ordering/duplicates → crop → measured background subtraction → optional Kα2
stripping → (optional) Savitzky–Golay for detection → baseline → peak detection + manual
edits → profile fitting on the *unsmoothed* net intensity → d-spacing, Scherrer, W–H,
reference comparison.
"""

from __future__ import annotations

import datetime as _dt
import math
import platform
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import scipy
from numpy.typing import NDArray

from .. import DISCLAIMER_EN, __version__
from ..models.dataset import XRDPattern, sha256_arrays
from ..models.recipe import AnalysisRecipe
from ..models.units import validate_wavelength
from . import preprocessing as proc
from . import profiles
from .crystallography import (
    BRAGG_FORMULA,
    NOT_CALCULABLE,
    SCHERRER_FORMULA,
    Derived,
    InstrumentBroadening,
    WHResult,
    bragg,
    scherrer,
    williamson_hall,
)
from .fit import AnalysisCancelled, FitResult, fit_peaks, peak_curve
from .peaks import (
    MIN_WIDTH_STEPS,
    PeakCandidate,
    apply_manual_edits,
    detect_peaks,
    drop_kalpha2_partners,
)
from .reference import MatchResult, ReferencePattern, match_reference

FloatArray = NDArray[np.float64]
ProgressFn = Callable[[float, str], None]
CancelFn = Callable[[], bool]

__all__ = ["AnalysisCancelled", "AnalysisResult", "Processed", "run_analysis", "run_batch"]


class AnalysisError(ValueError):
    pass


@dataclass
class Processed:
    two_theta_deg: FloatArray
    raw: FloatArray
    background_subtracted: FloatArray
    ka2_stripped: FloatArray | None
    smoothed: FloatArray | None
    baseline: FloatArray
    net: FloatArray
    sigma: FloatArray | None
    axis_report: proc.AxisReport
    uncertainty_model: list[str] = field(default_factory=list)

    def table(self) -> dict[str, FloatArray]:
        cols: dict[str, FloatArray] = {
            "two_theta_deg": self.two_theta_deg,
            "intensity_raw": self.raw,
            "after_measured_background": self.background_subtracted,
        }
        if self.ka2_stripped is not None:
            cols["after_ka2_strip"] = self.ka2_stripped
        if self.smoothed is not None:
            cols["smoothed_for_detection"] = self.smoothed
        cols["baseline"] = self.baseline
        cols["net"] = self.net
        return cols


@dataclass
class PeakReport:
    peak_id: int
    fit: dict[str, Any]
    d_spacing: Derived | None
    scherrer: Derived | None

    def row(self) -> dict[str, Any]:
        f = self.fit
        d = self.d_spacing
        s = self.scherrer
        return {
            "peak_id": self.peak_id,
            "source": f["source"],
            "profile": f["profile"],
            "two_theta_deg": f["center_deg"],
            "two_theta_err_deg": f["center_err"],
            "d_A": d.value if d else None,
            "d_err_A": d.uncertainty if d else None,
            "fwhm_deg": f["fwhm_deg"],
            "fwhm_err_deg": f["fwhm_err"],
            "eta": f.get("eta"),
            "height": f["height"],
            "height_err": f["height_err"],
            "area": f["area"],
            "area_err": f["area_err"],
            "scherrer_nm": s.value if s else None,
            "scherrer_err_nm": s.uncertainty if s else None,
            "scherrer_status": s.status if s else "",
            "scherrer_reason": s.reason if s else "",
            "flags": ";".join(f["flags"]),
        }


@dataclass
class AnalysisResult:
    dataset: dict[str, Any]
    recipe: dict[str, Any]
    processed: Processed
    candidates: list[PeakCandidate]
    detection_threshold: float
    fit: FitResult
    peaks: list[PeakReport]
    williamson_hall: WHResult | None
    references: list[MatchResult]
    warnings: list[str]
    notes: list[str]
    provenance: dict[str, Any]
    group_stats: list[dict[str, Any]] = field(default_factory=list)

    def peak_rows(self) -> list[dict[str, Any]]:
        return [p.row() for p in self.peaks]

    def removal_position(self, peak_id: int) -> float:
        """2θ to store in ``manual_remove_deg`` for fitted peak ``peak_id``: the position of
        the candidate it was seeded from (peak_id indexes ``candidates``)."""
        if 0 <= peak_id < len(self.candidates):
            return float(self.candidates[peak_id].position_deg)
        rep = next(p for p in self.peaks if p.peak_id == peak_id)
        return float(rep.fit["center_deg"])

    def to_dict(self, include_arrays: bool = False) -> dict[str, Any]:
        d: dict[str, Any] = {
            "format": "xrd-analyzer-result",
            "version": 1,
            "disclaimer": DISCLAIMER_EN,
            "dataset": self.dataset,
            "recipe": self.recipe,
            "detection_threshold": self.detection_threshold,
            "candidates": [c.to_dict() for c in self.candidates],
            "peaks": [
                {
                    "fit": p.fit,
                    "d_spacing": p.d_spacing.to_dict() if p.d_spacing else None,
                    "scherrer": p.scherrer.to_dict() if p.scherrer else None,
                }
                for p in self.peaks
            ],
            "fit_groups": [g.to_dict(include_arrays) for g in self.fit.groups],
            "williamson_hall": self.williamson_hall.to_dict() if self.williamson_hall else None,
            "references": [m.to_dict() for m in self.references],
            "axis_report": self.processed.axis_report.to_dict(),
            "uncertainty_model": self.processed.uncertainty_model,
            "warnings": self.warnings,
            "notes": self.notes,
            "provenance": self.provenance,
        }
        if include_arrays:
            d["processed"] = {k: v.tolist() for k, v in self.processed.table().items()}
        return d


def _instrument(recipe: AnalysisRecipe) -> InstrumentBroadening:
    s = recipe.size
    return InstrumentBroadening(
        s.instrument_mode, s.instrument_fwhm_deg, s.caglioti_U, s.caglioti_V, s.caglioti_W
    )


def provenance(
    pattern: XRDPattern, recipe: AnalysisRecipe, background: XRDPattern | None = None
) -> dict[str, Any]:
    pp = recipe.preprocess
    baseline_alg = {
        "none": "none",
        "snip": "SNIP with LLS transform (Ryan et al. 1988)",
        "als": "asymmetric least squares (Eilers & Boelens 2005)",
        "poly": "iterative modified polynomial (Lieber & Mahadevan-Jansen 2003)",
        "masked": "Whittaker smoother through non-peak points (peak mask from provisional "
        "SNIP + find_peaks)",
    }[pp.baseline_method]
    bg_info = None
    if background is not None:
        bg_info = {
            "name": background.name,
            "sha256": background.sha256,
            # hash of the normalised (2θ, I) arrays actually subtracted: differs when the same
            # file is imported with different axis/column/unit choices
            "array_sha256": sha256_arrays(background.two_theta_deg, background.intensity),
            "source_path": background.source_path,
            "intensity_unit": background.intensity_unit,
            "n_points": background.n_points,
            "parser": background.parser,
            "quality_flags": list(background.quality_flags),
            "x_column": background.metadata.get("x_column"),
            "y_column": background.metadata.get("y_column"),
            "scale": pp.background_scale,
        }
    return {
        "analysed_at_utc": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
        "software": f"xrd-analyzer {__version__}",
        "python": sys.version.split()[0],
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "platform": platform.platform(),
        "source_sha256": pattern.sha256,
        "source_array_sha256": sha256_arrays(pattern.two_theta_deg, pattern.intensity),
        "source_path": pattern.source_path,
        "measured_background": bg_info,
        "algorithms": {
            "baseline": baseline_alg,
            "kalpha2": {
                "none": "not handled",
                "strip": "Rachinger stripping",
                "fit_doublet": "constrained Kα1/Kα2 doublet fit",
            }[pp.kalpha2_mode],
            "smoothing": "Savitzky–Golay (detection only)" if pp.smoothing_enabled else "none",
            "peak_detection": "scipy.signal.find_peaks (height & prominence ≥ k·σ; "
            "σ = Poisson for counts, rolling MAD otherwise)",
            "fitting": f"scipy.optimize.least_squares (trf), profile={recipe.fit.profile}",
            "fwhm_voigt": "Olivero & Longbothum (1977)",
            "variance_model": "Poisson (counts) propagated through duplicate averaging and "
            "background interpolation; residual-scaled after Kα2 stripping",
        },
    }


def preprocess(
    pattern: XRDPattern, recipe: AnalysisRecipe, background: XRDPattern | None = None
) -> tuple[Processed, list[str], list[str]]:
    pp = recipe.preprocess
    warnings: list[str] = []
    notes: list[str] = []
    x, y, axis = proc.order_axis(pattern.two_theta_deg, pattern.intensity, pp.duplicate_policy)
    n_avg = axis.n_per_point if axis.n_per_point is not None else np.ones_like(x)
    notes += axis.notes
    if pp.crop_min_deg is not None or pp.crop_max_deg is not None:
        x, y, keep = proc.crop(x, y, pp.crop_min_deg, pp.crop_max_deg, return_mask=True)
        n_avg = n_avg[keep]
        axis.update_steps(x)  # sampling statistics must describe the cropped grid
    if math.isfinite(axis.step_cv) and axis.step_cv > 0.05:
        warnings.append(
            f"non-uniform 2θ step (CV {axis.step_cv:.1%}); Savitzky–Golay and SNIP "
            "assume uniform sampling"
        )
    raw = y
    umodel: list[str] = []
    # Poisson variance of each (possibly averaged) point: Var(mean of n) = λ/n
    var = np.maximum(raw, 1.0) / n_avg if pattern.counts_like else None
    if var is None:
        notes.append(
            f"intensity unit '{pattern.intensity_unit}' is not raw counts: "
            "unweighted fit, σ estimated from residuals"
        )
        umodel.append(
            "unweighted fit: σ estimated from fit residuals; χ²_red is a variance estimate, "
            "not a goodness-of-fit test"
        )
    else:
        umodel.append(
            "Poisson weights σ = √counts (duplicate averages: √(λ/n)); covariance scaled by "
            "max(1, χ²_red)"
        )
    if background is not None:
        if background.intensity_unit.strip().lower() != pattern.intensity_unit.strip().lower():
            raise proc.ProcessingError(
                f"measured background unit '{background.intensity_unit}' differs from the "
                f"pattern unit '{pattern.intensity_unit}'; convert one of them first"
            )
        bx, by, brep = proc.order_axis(background.two_theta_deg, background.intensity, "average")
        bn = brep.n_per_point if brep.n_per_point is not None else np.ones_like(bx)
        bvar = np.maximum(by, 1.0) / bn
        b_on_x, b_var_on_x = proc.interp_with_variance(x, bx, by, bvar)
        y = y - pp.background_scale * b_on_x
        notes.append(
            f"measured background '{background.name}' (SHA-256 {str(background.sha256)[:12]}…) "
            f"subtracted (scale {pp.background_scale})"
        )
        if var is not None:
            var = var + pp.background_scale**2 * b_var_on_x
        j = np.clip(np.searchsorted(bx, x), 0, bx.size - 1)
        if not np.all(np.isclose(bx[j], x, rtol=0, atol=1e-9)):
            umodel.append(
                "measured background was interpolated: neighbouring points share background "
                "samples (correlated errors); fit uncertainties and χ² ignore these "
                "correlations (approximate)"
            )
    bg_sub = y
    stripped = None
    if pp.kalpha2_mode == "strip":
        assert pp.wavelength_A is not None and pp.wavelength2_A is not None
        stripped = proc.strip_kalpha2(x, y, pp.wavelength_A, pp.wavelength2_A, pp.ka2_ratio)
        y = stripped
        notes.append("Kα2 removed by Rachinger stripping (assumes identical Kα1/Kα2 profiles)")
        # applies to every intensity unit: the transform itself correlates the noise
        var = None
        warnings.append(
            "Kα2 stripping correlates the noise of neighbouring points: fit uncertainties "
            "are residual-scaled and ignore these correlations (prefer 'fit_doublet', "
            "which keeps the original noise model)"
        )
        umodel = [
            "Kα2-stripped data: unweighted fit, σ from residuals; correlated noise ignored — "
            "uncertainties are approximate and χ²_red is not a goodness-of-fit test"
        ] + [m for m in umodel if "background" in m]
    elif pp.kalpha2_mode == "none":
        notes.append(
            "Kα2 not handled: if the source is not monochromatic, high-angle peak "
            "positions/widths include the Kα2 component"
        )
    sigma = np.sqrt(var) if var is not None else None
    smoothed = None
    if pp.smoothing_enabled:
        smoothed = proc.savgol(y, pp.smoothing_window, pp.smoothing_polyorder)
    base_src = smoothed if smoothed is not None else y
    if pp.baseline_method == "snip":
        baseline = proc.baseline_snip(base_src, pp.snip_iterations)
    elif pp.baseline_method == "als":
        baseline = proc.baseline_als(base_src, pp.als_lambda, pp.als_p, pp.als_iterations)
    elif pp.baseline_method == "poly":
        baseline = proc.baseline_poly(x, base_src, pp.poly_degree, pp.poly_iterations)
    elif pp.baseline_method == "masked":
        # provisional SNIP baseline → provisional peaks → Whittaker through non-peak points
        prov = proc.baseline_snip(base_src, pp.snip_iterations)
        ps = recipe.peaks
        cands, _ = detect_peaks(
            x,
            base_src - prov,
            y - prov,
            ps.prominence_sigma,
            ps.min_distance_deg,
            ps.min_width_deg,
            ps.max_peaks,
            sigma if sigma is not None else proc.local_noise_sigma(y - prov),
            ps.max_width_deg,
        )
        manual = [(c, 10 * float(np.median(np.diff(x)))) for c in ps.manual_add_deg]
        centers = [c.position_deg for c in cands] + [m[0] for m in manual]
        fwhms = [c.fwhm_est_deg for c in cands] + [m[1] for m in manual]
        mask = proc.peak_mask(x, centers, fwhms, pp.mask_fwhm_factor)
        baseline = proc.baseline_masked_whittaker(base_src, mask, pp.als_lambda)
        notes.append(
            f"masked baseline: {len(centers)} provisional peak region(s) excluded "
            f"(±{pp.mask_fwhm_factor}×FWHM, {mask.mean():.0%} of points); amorphous/broad "
            "features outside the mask are part of the baseline"
        )
    else:
        baseline = np.zeros_like(y)
    warnings += proc.baseline_warnings(y, baseline)
    net = y - baseline
    return (
        Processed(x, raw, bg_sub, stripped, smoothed, baseline, net, sigma, axis, umodel),
        warnings,
        notes,
    )


def doublet_observable_height(p: Any, doublet: tuple[float, float, float]) -> float:
    """Maximum of the modelled Kα1+Kα2 signal of fitted peak ``p``.

    The grid always contains both component centres and is sampled at FWHM/40 between
    them (±3 FWHM), so the estimate can never fall below the model value at the Kα1 centre,
    however narrow the peak.
    """
    c1, w = float(p.center_deg), max(float(p.fwhm_deg), 1e-9)
    c2 = profiles.kalpha2_position(c1, doublet[0], doublet[1])
    centres = [c1] + ([c2] if math.isfinite(c2) else [])
    lo, hi = min(centres) - 3 * w, max(centres) + 3 * w
    n = int(min(max(math.ceil((hi - lo) / (w / 40.0)) + 1, 201), 200_001))
    grid = np.unique(np.concatenate([np.linspace(lo, hi, n), np.asarray(centres)]))
    return float(np.max(peak_curve(grid, p, doublet)))


def run_analysis(
    pattern: XRDPattern,
    recipe: AnalysisRecipe,
    background: XRDPattern | None = None,
    references: Sequence[ReferencePattern] = (),
    progress: ProgressFn | None = None,
    cancel: CancelFn | None = None,
) -> AnalysisResult:
    def step(frac: float, msg: str) -> None:
        if cancel is not None and cancel():
            raise AnalysisCancelled("analysis cancelled by user")
        if progress is not None:
            progress(frac, msg)

    problems = recipe.validate()
    if problems:
        raise AnalysisError("invalid recipe: " + "; ".join(problems))
    try:
        wl = validate_wavelength(recipe.preprocess.wavelength_A)
    except ValueError as exc:
        raise AnalysisError(str(exc)) from exc
    pp, ps, pf, sz = recipe.preprocess, recipe.peaks, recipe.fit, recipe.size

    step(0.05, "preprocessing")
    try:
        processed, warnings, notes = preprocess(pattern, recipe, background)
    except proc.ProcessingError as exc:
        raise AnalysisError(str(exc)) from exc
    x, net = processed.two_theta_deg, processed.net
    det_src = (processed.smoothed - processed.baseline) if processed.smoothed is not None else net

    step(0.25, "peak detection")
    cands: list[PeakCandidate] = []
    threshold = math.nan
    point_sigma = processed.sigma if processed.sigma is not None else proc.local_noise_sigma(net)
    if ps.auto_detect:
        cands, threshold = detect_peaks(
            x,
            det_src,
            net,
            ps.prominence_sigma,
            ps.min_distance_deg,
            ps.min_width_deg,
            ps.max_peaks,
            point_sigma,
            ps.max_width_deg,
        )
        if pp.kalpha2_mode == "fit_doublet":
            assert pp.wavelength2_A is not None
            cands, n_drop = drop_kalpha2_partners(cands, wl, pp.wavelength2_A)
            if n_drop:
                notes.append(
                    f"{n_drop} detected Kα2 partner peak(s) merged into their Kα1 doublets"
                )
    cands, edit_notes = apply_manual_edits(
        cands, x, det_src, ps.manual_add_deg, ps.manual_remove_deg, ps.edit_tolerance_deg
    )
    notes += edit_notes
    if not cands:
        warnings.append("no peaks found (lower the prominence threshold or add peaks manually)")

    step(0.4, f"fitting {len(cands)} peak(s)")
    doublet = None
    if pp.kalpha2_mode == "fit_doublet":
        assert pp.wavelength2_A is not None
        doublet = (wl, pp.wavelength2_A, pp.ka2_ratio)

    def _fit(cs: list[PeakCandidate]) -> FitResult:
        return fit_peaks(
            x,
            net,
            cs,
            pf.profile,
            pf.window_fwhm_factor,
            pf.local_background,
            processed.sigma,
            doublet,
            pf.max_nfev,
            cancel,
        )

    fit = _fit(cands)

    # Auto-detected candidates are confirmed by the fit with the SAME significance criterion
    # used for detection (fitted amplitude ≥ k·σ at the peak), and must be resolvable at the
    # local scan step and have height ≥ 3σ_height. Otherwise they are noise: drop them and
    # refit once. Manual peaks are the user's decision and are kept (with flags).
    def _observable_height(p: Any) -> float:
        # Detection saw the summed signal; for a Kα1/Kα2 doublet compare the maximum of the
        # modelled doublet (both components), not the Kα1 component alone.
        if fit.doublet is None or not p.doublet:
            return float(p.height)
        return doublet_observable_height(p, fit.doublet)

    def _unconfirmed(p: Any) -> bool:
        thr_here = ps.prominence_sigma * float(np.interp(p.center_deg, x, point_sigma))
        return bool(
            p.fwhm_deg < MIN_WIDTH_STEPS * proc.local_step(x, p.center_deg)
            or _observable_height(p) < thr_here
            or (p.height_err is not None and p.height < 3 * p.height_err)
        )

    noise_ids = {
        p.peak_id
        for p in fit.peaks
        if cands[p.peak_id].source == "auto" and p.ok and _unconfirmed(p)
    }
    if noise_ids:
        cands = [c for i, c in enumerate(cands) if i not in noise_ids]
        notes.append(
            f"{len(noise_ids)} auto-detected candidate(s) not confirmed by the fit (fitted "
            f"height < {ps.prominence_sigma}σ, < 3σ_height, or FWHM < {MIN_WIDTH_STEPS} steps) "
            "were rejected and the rest refitted"
        )
        fit = _fit(cands)
    warnings += fit.warnings

    step(0.8, "crystallographic quantities")
    inst = _instrument(recipe)
    reports: list[PeakReport] = []
    for p in fit.peaks:
        if not p.ok or not 0 < p.center_deg < 180:
            reports.append(PeakReport(p.peak_id, p.to_dict(), None, None))
            continue
        if "not_converged" in p.flags:
            reason = "profile fit did not converge (increase max_nfev or check the peak)"
            reports.append(
                PeakReport(
                    p.peak_id,
                    p.to_dict(),
                    Derived(
                        "d-spacing", None, None, "Å", NOT_CALCULABLE, BRAGG_FORMULA, [], reason
                    ),
                    Derived(
                        "Scherrer crystallite size",
                        None,
                        None,
                        "nm",
                        NOT_CALCULABLE,
                        SCHERRER_FORMULA,
                        [],
                        reason,
                    ),
                )
            )
            continue
        reports.append(
            PeakReport(
                p.peak_id,
                p.to_dict(),
                bragg(p.center_deg, wl, p.center_err),
                scherrer(
                    p.center_deg, p.fwhm_deg, p.fwhm_err, sz.scherrer_K, wl, inst, sz.correction
                ),
            )
        )
    good = [p for p in fit.peaks if p.eligible]
    if any(p.ok and not p.eligible for p in fit.peaks):
        warnings.append(
            "peaks with non-converged or non-identifiable fits were excluded from "
            "Williamson–Hall and the reference comparison"
        )
    wh = williamson_hall(
        [(p.center_deg, p.fwhm_deg, p.fwhm_err) for p in good],
        sz.scherrer_K,
        wl,
        inst,
        sz.correction,
        sz.wh_min_peaks,
    )
    if wh.status != NOT_CALCULABLE:
        notes.append(
            "Williamson–Hall used all well-fitted peaks; it assumes they belong to a "
            "single phase — exclude peaks of other phases manually"
        )
    for g in fit.groups:
        for fl in g.flags:
            warnings.append(f"fit group {g.group_id} ({g.lo_deg:.2f}–{g.hi_deg:.2f}°): {fl}")

    step(0.9, "reference comparison")
    obs = [p.center_deg for p in good]
    matches = [
        match_reference(
            obs,
            ref,
            wl,
            (float(x[0]), float(x[-1])),
            recipe.reference.tolerance_deg,
            recipe.reference.min_rel_intensity,
        )
        for ref in references
    ]
    if pattern.synthetic:
        notes.insert(0, "SYNTHETIC EXAMPLE DATA — not an instrument measurement")
    step(1.0, "done")
    return AnalysisResult(
        dataset=pattern.summary(),
        recipe=recipe.to_dict(),
        processed=processed,
        candidates=cands,
        detection_threshold=threshold,
        fit=fit,
        peaks=reports,
        williamson_hall=wh,
        references=matches,
        warnings=warnings,
        notes=notes,
        provenance=provenance(pattern, recipe, background),
        group_stats=[g.to_dict() for g in fit.groups],
    )


@dataclass
class BatchItem:
    name: str
    result: AnalysisResult | None
    error: str | None


def run_batch(
    patterns: Sequence[XRDPattern],
    recipe: AnalysisRecipe,
    references: Sequence[ReferencePattern] = (),
    progress: ProgressFn | None = None,
    cancel: CancelFn | None = None,
    backgrounds: Sequence[XRDPattern | None] | None = None,
) -> list[BatchItem]:
    """Analyse many patterns with one recipe. A failing pattern does not stop the batch.

    ``backgrounds`` (optional, same length as ``patterns``) gives each pattern its own
    measured background, so batch and single analyses are identical.
    """
    out: list[BatchItem] = []
    n = len(patterns)
    if backgrounds is not None and len(backgrounds) != n:
        raise AnalysisError("backgrounds must have one entry (or None) per pattern")
    for i, pat in enumerate(patterns):
        if cancel is not None and cancel():
            raise AnalysisCancelled("batch cancelled by user")
        if progress is not None:
            progress(i / max(n, 1), f"{pat.name} ({i + 1}/{n})")
        try:
            out.append(
                BatchItem(
                    pat.name,
                    run_analysis(
                        pat,
                        recipe,
                        backgrounds[i] if backgrounds is not None else None,
                        references,
                        cancel=cancel,
                    ),
                    None,
                )
            )
        except AnalysisCancelled:
            raise
        except Exception as exc:
            out.append(BatchItem(pat.name, None, f"{type(exc).__name__}: {exc}"))
    if progress is not None:
        progress(1.0, "batch done")
    return out


def batch_peak_statistics(
    results: Sequence[AnalysisResult], tolerance_deg: float = 0.1
) -> list[dict[str, Any]]:
    """Group fitted peaks of several patterns into reflections and report N, mean and sample
    standard deviation of 2θ, d and FWHM.

    Groups are bounded: every member lies within ``tolerance_deg`` of the group's first
    (lowest-angle) member, so no chaining. Each pattern contributes at most one peak per
    group; a second nearby peak of the same pattern starts a new group. N is the number of
    contributing patterns. Only converged, identifiable peaks are used; std is None if N < 2.
    """
    pts: list[tuple[float, float, float, str]] = []
    for k, r in enumerate(results):
        name = f"{k}:{r.dataset.get('name')}"
        for p in r.peaks:
            f = p.fit
            if (
                p.d_spacing is None
                or p.d_spacing.value is None
                or not math.isfinite(f["center_deg"])
                or {"parameters_not_identifiable", "not_converged"} & set(f["flags"])
            ):
                continue
            pts.append((f["center_deg"], float(p.d_spacing.value), f["fwhm_deg"], name))
    pts.sort()
    groups: list[list[tuple[float, float, float, str]]] = []
    for pt in pts:
        g = groups[-1] if groups else None
        if g is not None and pt[0] - g[0][0] <= tolerance_deg and pt[3] not in {q[3] for q in g}:
            g.append(pt)
        else:
            groups.append([pt])
    rows: list[dict[str, Any]] = []
    for g in groups:
        tt = np.array([q[0] for q in g])
        dd = np.array([q[1] for q in g])
        ww = np.array([q[2] for q in g])
        n = len(g)

        def sd(a: FloatArray, n: int = n) -> float | None:
            return float(np.std(a, ddof=1)) if n > 1 else None

        rows.append(
            {
                "n_patterns": n,
                "patterns": [q[3].split(":", 1)[1] for q in g],
                "two_theta_mean_deg": float(tt.mean()),
                "two_theta_std_deg": sd(tt),
                "two_theta_spread_deg": float(np.ptp(tt)),
                "d_mean_A": float(dd.mean()),
                "d_std_A": sd(dd),
                "fwhm_mean_deg": float(ww.mean()),
                "fwhm_std_deg": sd(ww),
            }
        )
    return rows
