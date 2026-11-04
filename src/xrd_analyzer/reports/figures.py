"""Matplotlib figures built on ``matplotlib.figure.Figure`` (no pyplot → thread-safe)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
from matplotlib.figure import Figure

from ..core.analysis import AnalysisResult
from ..core.fit import peak_curve

LIGHT = {
    "bg": "#ffffff",
    "fg": "#1f2328",
    "grid": "#d0d7de",
    "raw": "#57606a",
    "base": "#bf8700",
    "net": "#0969da",
    "fit": "#cf222e",
    "peak": "#8250df",
    "resid": "#57606a",
}
DARK = {
    "bg": "#1e1f22",
    "fg": "#e6edf3",
    "grid": "#3d444d",
    "raw": "#9198a1",
    "base": "#d29922",
    "net": "#4493f8",
    "fit": "#f85149",
    "peak": "#ab7df8",
    "resid": "#9198a1",
}
OVERLAY_COLORS = [
    "#0969da",
    "#cf222e",
    "#1a7f37",
    "#8250df",
    "#bf8700",
    "#1b7c83",
    "#d1242f",
    "#6639ba",
]


@dataclass
class PlotSettings:
    title: str = ""
    xlabel: str = "2θ (deg)"
    ylabel: str = "Intensity"
    font_size: float = 10.0
    show_legend: bool = True
    show_raw: bool = True
    show_baseline: bool = True
    show_net: bool = False
    show_fit: bool = True
    show_components: bool = False
    show_residual: bool = True
    show_peak_markers: bool = True
    log_y: bool = False
    dark: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PlotSettings:
        """Unknown fields, and fields of the wrong kind as in a damaged file, are left out."""
        if not isinstance(d, dict):
            return cls()
        defaults = cls()
        known: dict[str, Any] = {}
        for k, v in d.items():
            default = getattr(defaults, k, None)
            if k == "font_size":
                if isinstance(v, int | float) and not isinstance(v, bool) and 1 <= v <= 200:
                    known[k] = float(v)
            elif k in cls.__dataclass_fields__ and type(v) is type(default):
                known[k] = v
        return cls(**known)


def _style(ax: Any, pal: dict[str, str], fs: float) -> None:
    ax.set_facecolor(pal["bg"])
    ax.tick_params(colors=pal["fg"], labelsize=fs * 0.9)
    for sp in ax.spines.values():
        sp.set_color(pal["grid"])
    ax.xaxis.label.set_color(pal["fg"])
    ax.yaxis.label.set_color(pal["fg"])
    ax.title.set_color(pal["fg"])
    ax.grid(True, color=pal["grid"], linewidth=0.5, alpha=0.6)


def draw_result(fig: Figure, result: AnalysisResult, s: PlotSettings | None = None) -> None:
    """Draw pattern, baseline, fitted model, peak markers and residuals into ``fig``."""
    s = s or PlotSettings()
    pal = DARK if s.dark else LIGHT
    fig.clear()
    fig.set_facecolor(pal["bg"])
    pr = result.processed
    x = pr.two_theta_deg
    if s.show_residual:
        gs = fig.add_gridspec(2, 1, height_ratios=[4, 1], hspace=0.05)
        ax = fig.add_subplot(gs[0])
        axr = fig.add_subplot(gs[1], sharex=ax)
    else:
        ax = fig.add_subplot(111)
        axr = None
    signal = pr.ka2_stripped if pr.ka2_stripped is not None else pr.background_subtracted
    if s.show_raw:
        ax.plot(x, pr.raw, color=pal["raw"], lw=0.7, label="raw")
        if pr.ka2_stripped is not None:
            ax.plot(
                x, pr.ka2_stripped, color=pal["net"], lw=0.6, alpha=0.6, label="after Kα2 strip"
            )
    if pr.smoothed is not None:
        ax.plot(
            x, pr.smoothed, color=pal["peak"], lw=0.7, alpha=0.7, label="Savitzky–Golay (detection)"
        )
    if s.show_baseline and np.any(pr.baseline):
        ax.plot(x, pr.baseline, color=pal["base"], lw=1.0, ls="--", label="baseline")
    if s.show_net:
        ax.plot(x, pr.net, color=pal["net"], lw=0.7, label="net")
    model = result.fit.model(x)
    if s.show_fit and result.fit.peaks:
        ax.plot(
            x, pr.baseline + model + _local_bg(result, x), color=pal["fit"], lw=1.0, label="fit"
        )
    if s.show_components:
        for p in result.fit.peaks:
            if p.ok:
                ax.plot(x, pr.baseline + peak_curve(x, p, result.fit.doublet), lw=0.6, alpha=0.7)
    if s.show_peak_markers:
        for p in result.fit.peaks:
            if p.ok:
                yv = float(np.interp(p.center_deg, x, signal))
                ax.plot(
                    [p.center_deg], [yv], marker="v", color=pal["peak"], ms=5, ls="none", label=None
                )
                ax.annotate(
                    f"{p.peak_id}",
                    (p.center_deg, yv),
                    textcoords="offset points",
                    xytext=(0, 6),
                    ha="center",
                    fontsize=s.font_size * 0.75,
                    color=pal["peak"],
                )
    if axr is not None:
        resid = np.full_like(x, np.nan)
        for g in result.fit.groups:
            if g.x is not None and g.y is not None and g.y_fit is not None:
                m = np.isin(x, g.x)
                resid[m] = g.y - g.y_fit
        axr.plot(x, resid, color=pal["resid"], lw=0.6)
        axr.axhline(0, color=pal["grid"], lw=0.8)
        axr.set_ylabel("resid.", fontsize=s.font_size)
        axr.set_xlabel(s.xlabel, fontsize=s.font_size)
        _style(axr, pal, s.font_size)
        ax.tick_params(labelbottom=False)
    else:
        ax.set_xlabel(s.xlabel, fontsize=s.font_size)
    if s.log_y:
        ax.set_yscale("symlog", linthresh=10)
    ax.set_ylabel(f"{s.ylabel} ({result.dataset.get('intensity_unit', '')})", fontsize=s.font_size)
    title = s.title or result.dataset.get("name", "")
    if result.dataset.get("synthetic"):
        title += "  [SYNTHETIC EXAMPLE]"
    ax.set_title(title, fontsize=s.font_size * 1.1)
    _style(ax, pal, s.font_size)
    if s.show_legend:
        leg = ax.legend(fontsize=s.font_size * 0.8, frameon=False)
        for t in leg.get_texts():
            t.set_color(pal["fg"])


def _local_bg(result: AnalysisResult, x: np.ndarray) -> np.ndarray:
    out = np.zeros_like(x)
    for g in result.fit.groups:
        if g.x is None or not g.background:
            continue
        m = (x >= g.lo_deg) & (x <= g.hi_deg)
        xm = float(np.mean(g.x))
        val = g.background[0] + (g.background[1] * (x[m] - xm) if len(g.background) > 1 else 0)
        out[m] = val
    return out


def draw_overlay(
    fig: Figure,
    patterns: list[tuple[str, np.ndarray, np.ndarray]],
    s: PlotSettings | None = None,
    normalise: bool = False,
    offset: float = 0.0,
) -> None:
    """Overlay several raw patterns (name, 2θ, intensity)."""
    s = s or PlotSettings()
    pal = DARK if s.dark else LIGHT
    fig.clear()
    fig.set_facecolor(pal["bg"])
    ax = fig.add_subplot(111)
    for k, (name, x, y) in enumerate(patterns):
        yy = np.asarray(y, dtype=float)
        if normalise and np.max(yy) > 0:
            yy = yy / np.max(yy)
        ax.plot(
            x, yy + k * offset, lw=0.8, color=OVERLAY_COLORS[k % len(OVERLAY_COLORS)], label=name
        )
    ax.set_xlabel(s.xlabel, fontsize=s.font_size)
    ax.set_ylabel("normalised intensity" if normalise else s.ylabel, fontsize=s.font_size)
    ax.set_title(s.title or "overlay", fontsize=s.font_size * 1.1)
    if s.log_y:
        ax.set_yscale("symlog", linthresh=10)
    _style(ax, pal, s.font_size)
    if s.show_legend and patterns:
        leg = ax.legend(fontsize=s.font_size * 0.8, frameon=False)
        for t in leg.get_texts():
            t.set_color(pal["fg"])


def draw_wh(fig: Figure, result: AnalysisResult, s: PlotSettings | None = None) -> bool:
    s = s or PlotSettings()
    pal = DARK if s.dark else LIGHT
    wh = result.williamson_hall
    fig.clear()
    fig.set_facecolor(pal["bg"])
    ax = fig.add_subplot(111)
    _style(ax, pal, s.font_size)
    if wh is None or not wh.x:
        ax.text(
            0.5,
            0.5,
            "Williamson–Hall: not computable" + (f"\n{wh.reason}" if wh else ""),
            ha="center",
            va="center",
            transform=ax.transAxes,
            color=pal["fg"],
            wrap=True,
        )
        return False
    yerr = [e if e is not None else 0.0 for e in wh.y_err]
    ax.errorbar(
        wh.x, np.array(wh.y) * 1e3, yerr=np.array(yerr) * 1e3, fmt="o", color=pal["net"], capsize=3
    )
    if wh.intercept is not None and wh.slope is not None:
        xx = np.linspace(0, max(wh.x) * 1.05, 50)
        ax.plot(xx, (wh.intercept + wh.slope * xx) * 1e3, color=pal["fit"], lw=1)
    ax.set_xlabel("4 sin θ", fontsize=s.font_size)
    ax.set_ylabel("β cos θ (mrad)", fontsize=s.font_size)
    txt = f"status: {wh.status}"
    if wh.size and wh.size.value is not None:
        txt += f"\nD = {wh.size.value:.2f} ± {wh.size.uncertainty or float('nan'):.2f} nm"
    if wh.strain and wh.strain.value is not None:
        txt += f"\nε = {wh.strain.value:.2e} ± {wh.strain.uncertainty or float('nan'):.1e}"
    ax.text(
        0.02,
        0.97,
        txt,
        transform=ax.transAxes,
        va="top",
        fontsize=s.font_size * 0.85,
        color=pal["fg"],
    )
    ax.set_title("Williamson–Hall (uniform deformation model)", fontsize=s.font_size * 1.1)
    return True


def save_figure(fig: Figure, path: str | Path, dpi: int = 300) -> Path:
    p = Path(path)
    if p.suffix.lower() not in {".png", ".svg", ".pdf"}:
        raise ValueError("figure format must be .png, .svg or .pdf")
    fig.savefig(p, dpi=dpi, bbox_inches="tight", facecolor=fig.get_facecolor())
    return p


def result_figure(result: AnalysisResult, s: PlotSettings | None = None) -> Figure:
    fig = Figure(figsize=(8, 5.5))
    draw_result(fig, result, s)
    return fig
