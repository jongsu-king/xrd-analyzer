"""Analysis recipe: every user-chosen parameter, serialisable to JSON for reproducibility.

The recipe also stores manual peak edits, so re-running a recipe on the same raw file
reproduces the same peak list exactly.
"""

from __future__ import annotations

import dataclasses
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

RECIPE_FORMAT = "xrd-analyzer-recipe"
RECIPE_VERSION = 1

DUPLICATE_POLICIES = ("error", "average", "keep_first")
KA2_MODES = ("none", "strip", "fit_doublet")
BASELINE_METHODS = ("none", "snip", "als", "poly", "masked")
PROFILES = ("pseudo_voigt", "voigt")
LOCAL_BACKGROUNDS = ("none", "constant", "linear")
INSTRUMENT_MODES = ("none", "constant", "caglioti")
BROADENING_CORRECTIONS = ("quadratic", "linear")


@dataclass
class PreprocessSettings:
    wavelength_A: float | None = None
    duplicate_policy: str = "error"
    crop_min_deg: float | None = None
    crop_max_deg: float | None = None
    background_scale: float = 1.0
    kalpha2_mode: str = "none"
    wavelength2_A: float | None = None
    ka2_ratio: float = 0.5
    smoothing_enabled: bool = False
    smoothing_window: int = 11
    smoothing_polyorder: int = 3
    baseline_method: str = "snip"
    snip_iterations: int = 40
    als_lambda: float = 1e6
    als_p: float = 0.01
    als_iterations: int = 15
    poly_degree: int = 4
    poly_iterations: int = 100
    mask_fwhm_factor: float = 3.0


@dataclass
class PeakSettings:
    auto_detect: bool = True
    prominence_sigma: float = 6.0
    min_distance_deg: float = 0.15
    min_width_deg: float = 0.0
    max_width_deg: float | None = None
    max_peaks: int = 40
    manual_add_deg: list[float] = field(default_factory=list)
    manual_remove_deg: list[float] = field(default_factory=list)
    edit_tolerance_deg: float = 0.05


@dataclass
class FitSettings:
    profile: str = "pseudo_voigt"
    window_fwhm_factor: float = 4.0
    local_background: str = "linear"
    max_nfev: int = 4000


@dataclass
class SizeSettings:
    scherrer_K: float | None = None
    instrument_mode: str = "none"
    instrument_fwhm_deg: float | None = None
    caglioti_U: float | None = None
    caglioti_V: float | None = None
    caglioti_W: float | None = None
    correction: str = "quadratic"
    wh_min_peaks: int = 3


@dataclass
class ReferenceSettings:
    tolerance_deg: float = 0.10
    min_rel_intensity: float = 5.0


@dataclass
class AnalysisRecipe:
    preprocess: PreprocessSettings = field(default_factory=PreprocessSettings)
    peaks: PeakSettings = field(default_factory=PeakSettings)
    fit: FitSettings = field(default_factory=FitSettings)
    size: SizeSettings = field(default_factory=SizeSettings)
    reference: ReferenceSettings = field(default_factory=ReferenceSettings)

    def validate(self) -> list[str]:
        """Return a list of human-readable problems (empty if the recipe is consistent)."""
        p, s, f = self.preprocess, self.size, self.fit
        errs: list[str] = []
        for sec in ("preprocess", "peaks", "fit", "size", "reference"):
            _check_types(errs, sec, getattr(self, sec))
        if errs:
            return errs  # further checks assume well-typed finite numbers
        _check_choice(errs, "duplicate_policy", p.duplicate_policy, DUPLICATE_POLICIES)
        _check_choice(errs, "kalpha2_mode", p.kalpha2_mode, KA2_MODES)
        _check_choice(errs, "baseline_method", p.baseline_method, BASELINE_METHODS)
        _check_choice(errs, "profile", f.profile, PROFILES)
        _check_choice(errs, "local_background", f.local_background, LOCAL_BACKGROUNDS)
        _check_choice(errs, "instrument_mode", s.instrument_mode, INSTRUMENT_MODES)
        _check_choice(errs, "correction", s.correction, BROADENING_CORRECTIONS)
        if p.kalpha2_mode != "none":
            if p.wavelength2_A is None:
                errs.append("Kα2 handling needs the Kα2 wavelength (wavelength2_A)")
            elif p.wavelength_A is not None and p.wavelength2_A <= p.wavelength_A:
                errs.append("Kα2 wavelength must be longer than the Kα1 wavelength")
            if not 0 < p.ka2_ratio < 1:
                errs.append("Kα2/Kα1 intensity ratio must be between 0 and 1")
        if p.smoothing_window < 3 or p.smoothing_window % 2 == 0:
            errs.append("Savitzky–Golay window must be an odd integer ≥ 3")
        if not 0 <= p.smoothing_polyorder < p.smoothing_window:
            errs.append("Savitzky–Golay polyorder must be < window")
        if (
            p.crop_min_deg is not None
            and p.crop_max_deg is not None
            and p.crop_min_deg >= p.crop_max_deg
        ):
            errs.append("crop range: minimum must be below maximum")
        if p.baseline_method == "masked" and p.mask_fwhm_factor <= 0:
            errs.append("peak-mask factor must be > 0 (× FWHM)")
        if p.background_scale < 0:
            errs.append("background scale must be ≥ 0")
        if s.scherrer_K is not None and not 0.5 <= s.scherrer_K <= 2.0:
            errs.append("Scherrer shape factor K is usually 0.62–1.84; got value outside 0.5–2")
        if s.instrument_mode == "constant" and (
            s.instrument_fwhm_deg is None or s.instrument_fwhm_deg < 0
        ):
            errs.append("constant instrumental broadening needs a FWHM ≥ 0 (degrees 2θ)")
        if s.instrument_mode == "caglioti" and None in (s.caglioti_U, s.caglioti_V, s.caglioti_W):
            errs.append("Caglioti instrumental broadening needs U, V and W (deg²)")
        pk = self.peaks
        if pk.max_width_deg is not None and pk.max_width_deg <= pk.min_width_deg:
            errs.append("maximum peak width must exceed the minimum peak width")
        if s.wh_min_peaks < 3:
            errs.append("Williamson–Hall needs at least 3 peaks")
        if f.window_fwhm_factor <= 0.5:
            errs.append("fit window factor must be > 0.5 FWHM")
        return errs

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": RECIPE_FORMAT,
            "version": RECIPE_VERSION,
            **dataclasses.asdict(self),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnalysisRecipe:
        if data.get("format") != RECIPE_FORMAT:
            raise ValueError("not an XRD Analyzer recipe (missing 'format': 'xrd-analyzer-recipe')")
        if data.get("version") != RECIPE_VERSION:
            raise ValueError(f"unsupported recipe version {data.get('version')!r}")
        return cls(
            preprocess=_build(PreprocessSettings, data.get("preprocess", {})),
            peaks=_build(PeakSettings, data.get("peaks", {})),
            fit=_build(FitSettings, data.get("fit", {})),
            size=_build(SizeSettings, data.get("size", {})),
            reference=_build(ReferenceSettings, data.get("reference", {})),
        )

    def copy(self) -> AnalysisRecipe:
        return AnalysisRecipe.from_dict(self.to_dict())

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> AnalysisRecipe:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"recipe file is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("recipe file must contain a JSON object")
        return cls.from_dict(data)


_T = TypeVar("_T")


def _build(kind: type[_T], values: Any) -> _T:
    if not isinstance(values, dict):
        raise ValueError(f"{kind.__name__}: expected an object")
    names = {f.name for f in dataclasses.fields(kind)}  # type: ignore[arg-type]
    unknown = set(values) - names
    if unknown:
        raise ValueError(f"{kind.__name__}: unknown keys {sorted(unknown)}")
    return kind(**values)


def _check_types(errs: list[str], sec: str, obj: Any) -> None:
    """Every numeric field must be a finite int/float (or None where allowed)."""
    for fld in dataclasses.fields(obj):
        v = getattr(obj, fld.name)
        ann = str(fld.type)
        where = f"{sec}.{fld.name}"
        if ann.startswith("list"):
            if not isinstance(v, list) or not all(
                isinstance(e, int | float) and not isinstance(e, bool) and math.isfinite(e)
                for e in v
            ):
                errs.append(f"{where} must be a list of finite numbers")
        elif ann == "bool":
            if not isinstance(v, bool):
                errs.append(f"{where} must be true/false")
        elif ann.startswith("int"):
            if v is None and "None" in ann:
                continue
            if not isinstance(v, int) or isinstance(v, bool):
                errs.append(f"{where} must be an integer, got {v!r}")
        elif "float" in ann:
            if v is None and "None" in ann:
                continue
            if not isinstance(v, int | float) or isinstance(v, bool) or not math.isfinite(v):
                errs.append(f"{where} must be a finite number, got {v!r}")
        elif ann == "str" and not isinstance(v, str):
            errs.append(f"{where} must be text")


def _check_choice(errs: list[str], name: str, value: str, allowed: tuple[str, ...]) -> None:
    if value not in allowed:
        errs.append(f"{name}={value!r} is not one of {allowed}")
