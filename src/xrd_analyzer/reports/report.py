"""Human-readable reports: multi-page PDF (matplotlib) and self-contained HTML.

Korean text (file names, sample names) needs a CJK font: the first installed font from
:data:`CJK_FONT_CANDIDATES` is used as a fallback after DejaVu, and TrueType fonts are
embedded (pdf.fonttype 42). If no CJK font is installed the PDF is still written and
:func:`cjk_font` returns None (Hangul would then render as empty boxes; HTML is unaffected).
"""

from __future__ import annotations

import base64
import html
import io
import math
import textwrap
from functools import lru_cache
from pathlib import Path
from typing import Any

import matplotlib as mpl
from matplotlib import font_manager
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.figure import Figure

from .. import APP_NAME, DISCLAIMER_EN, DISCLAIMER_KO
from ..core.analysis import AnalysisResult
from .figures import PlotSettings, draw_result, draw_wh

CJK_FONT_CANDIDATES = (
    "AppleGothic",
    "Apple SD Gothic Neo",
    "Malgun Gothic",
    "NanumGothic",
    "Noto Sans CJK KR",
    "Noto Sans KR",
    "Source Han Sans KR",
    "UnDotum",
    "Noto Sans CJK JP",
)


@lru_cache(maxsize=1)
def cjk_font() -> str | None:
    names = {f.name for f in font_manager.fontManager.ttflist}
    return next((c for c in CJK_FONT_CANDIDATES if c in names), None)


def font_families(mono: bool = False) -> list[str]:
    fam = ["DejaVu Sans Mono" if mono else "DejaVu Sans"]
    k = cjk_font()
    if k:
        fam.append(k)
    return fam


def _fmt(v: Any, nd: int = 4) -> str:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "—"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def summary_lines(result: AnalysisResult) -> list[str]:
    ds, rc, pv = result.dataset, result.recipe, result.provenance
    lines = [DISCLAIMER_EN, ""]
    if ds.get("synthetic"):
        lines += ["*** SYNTHETIC EXAMPLE DATA — not an instrument measurement ***", ""]
    lines += [
        f"Dataset: {ds.get('name')}   points: {ds.get('n_points')}   "
        f"2θ: {_fmt(ds.get('two_theta_min_deg'), 3)}–{_fmt(ds.get('two_theta_max_deg'), 3)}°",
        f"Source: {ds.get('source_path') or '(in memory)'}",
        f"SHA-256: {ds.get('sha256')}",
        f"Parser: {ds.get('parser')} [{ds.get('format_status')}]   "
        f"intensity unit: {ds.get('intensity_unit')}",
        f"Quality flags: {', '.join(ds.get('quality_flags') or []) or 'none'}",
        "",
        f"Analysed: {pv['analysed_at_utc']}   {pv['software']}   Python {pv['python']}   "
        f"NumPy {pv['numpy']}   SciPy {pv['scipy']}",
        f"Platform: {pv['platform']}",
        "Algorithms: " + "; ".join(f"{k}: {v}" for k, v in pv["algorithms"].items()),
        "",
        "Recipe:",
    ]
    for sec in ("preprocess", "peaks", "fit", "size", "reference"):
        lines.append(f"  [{sec}] " + ", ".join(f"{k}={v}" for k, v in rc[sec].items()))
    lines += ["", "Uncertainty model:"] + [
        f"  - {m}" for m in result.processed.uncertainty_model or ["not recorded"]
    ]
    lines += ["", "Warnings:"] + [f"  - {w}" for w in result.warnings or ["none"]]
    lines += ["", "Notes:"] + [f"  - {n}" for n in result.notes or ["none"]]
    wh = result.williamson_hall
    lines += ["", "Williamson–Hall:"]
    if wh is None:
        lines.append("  not run")
    else:
        lines.append(f"  status: {wh.status}  peaks used: {wh.n_peaks}  {wh.reason}")
        if wh.size:
            lines.append(
                f"  D = {_fmt(wh.size.value, 3)} ± {_fmt(wh.size.uncertainty, 3)} nm "
                f"[{wh.size.status}] {wh.size.reason}"
            )
        if wh.strain:
            lines.append(f"  ε = {_fmt(wh.strain.value, 6)} ± {_fmt(wh.strain.uncertainty, 6)}")
        if wh.r2 is not None:
            lines.append(f"  R² = {_fmt(wh.r2, 4)}   χ²_red = {_fmt(wh.chi2_red, 3)}")
        lines += [f"  excluded: {e}" for e in wh.excluded]
        lines += [f"  assumption: {a}" for a in wh.assumptions]
    if result.references:
        lines += ["", "Reference comparison (candidates only):"]
        for m in result.references:
            lines.append(
                f"  {m.reference}: {m.n_matched}/{m.n_expected} matched, "
                f"mean |Δ2θ| {_fmt(m.mean_abs_delta_deg, 4)}°, "
                f"confidence {m.confidence}"
            )
        lines += [f"  caveat: {c}" for c in result.references[0].caveats]
    return lines


def peak_table_lines(result: AnalysisResult) -> list[str]:
    hdr = (
        f"{'#':>3} {'src':<6} {'2θ (deg)':>14} {'d (Å)':>16} {'FWHM (deg)':>15} "
        f"{'area':>12} {'Scherrer D (nm)':>18}  flags"
    )
    out = [hdr, "-" * len(hdr)]
    rows = result.peak_rows()
    for r in rows:
        dsz = (
            f"{_fmt(r['scherrer_nm'], 2)}±{_fmt(r['scherrer_err_nm'], 2)}"
            if r["scherrer_nm"] is not None
            else f"n/a ({r['scherrer_status']})"
        )
        out.append(
            f"{r['peak_id']:>3} {r['source']:<6} "
            f"{_fmt(r['two_theta_deg'])}±{_fmt(r['two_theta_err_deg'])} "
            f"{_fmt(r['d_A'], 5)}±{_fmt(r['d_err_A'], 5)} "
            f"{_fmt(r['fwhm_deg'])}±{_fmt(r['fwhm_err_deg'])} "
            f"{_fmt(r['area'], 1):>12} {dsz:>18}  {r['flags']}"
        )
    reasons = sorted({r["scherrer_reason"] for r in rows if r["scherrer_reason"]})
    out += ["", "Scherrer notes:"] + [f"  - {s}" for s in reasons or ["none"]]
    first = next((p.scherrer for p in result.peaks if p.scherrer), None)
    if first:
        out += ["", f"Formula: {first.formula}"]
        out += [f"  assumption: {a}" for a in first.assumptions]
    return out


def _text_page(pdf: PdfPages, title: str, lines: list[str]) -> None:
    per_page = 58
    wrapped: list[str] = []
    for ln in lines:
        wrapped += textwrap.wrap(ln, 105) or [""]
    for start in range(0, max(len(wrapped), 1), per_page):
        fig = Figure(figsize=(8.27, 11.69))
        fig.text(
            0.06, 0.96, title, fontsize=13, weight="bold", va="top", fontfamily=font_families()
        )
        fig.text(
            0.06,
            0.93,
            "\n".join(wrapped[start : start + per_page]),
            fontsize=7.5,
            fontfamily=font_families(mono=True),
            va="top",
        )
        pdf.savefig(fig)


def write_pdf_report(
    result: AnalysisResult, path: str | Path, settings: PlotSettings | None = None
) -> Path:
    p = Path(path)
    s = PlotSettings.from_dict({**(settings or PlotSettings()).to_dict(), "dark": False})
    with mpl.rc_context({"pdf.fonttype": 42, "font.family": font_families()}), PdfPages(p) as pdf:
        _text_page(pdf, f"{APP_NAME} report", summary_lines(result))
        fig = Figure(figsize=(11.69, 8.27))
        draw_result(fig, result, s)
        pdf.savefig(fig)
        _text_page(pdf, "Peak table", peak_table_lines(result))
        fig = Figure(figsize=(8.27, 6))
        if draw_wh(fig, result, s):
            pdf.savefig(fig)
        info = pdf.infodict()
        info["Title"] = f"XRD analysis — {result.dataset.get('name')}"
        info["Subject"] = DISCLAIMER_EN
    return p


def _png_b64(fig: Figure) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight", facecolor="white")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def write_html_report(
    result: AnalysisResult, path: str | Path, settings: PlotSettings | None = None
) -> Path:
    """Self-contained UTF-8 HTML (figures embedded as base64 PNG; no external resources)."""
    p = Path(path)
    s = PlotSettings.from_dict({**(settings or PlotSettings()).to_dict(), "dark": False})
    esc = html.escape
    with mpl.rc_context({"font.family": font_families()}):
        fig = Figure(figsize=(9, 6))
        draw_result(fig, result, s)
        pattern_png = _png_b64(fig)
        fig = Figure(figsize=(7, 5))
        wh_png = _png_b64(fig) if draw_wh(fig, result, s) else None
    cols = [
        "peak_id",
        "source",
        "two_theta_deg",
        "two_theta_err_deg",
        "d_A",
        "d_err_A",
        "fwhm_deg",
        "fwhm_err_deg",
        "area",
        "scherrer_nm",
        "scherrer_err_nm",
        "scherrer_status",
        "flags",
    ]
    head = "".join(f"<th>{esc(c)}</th>" for c in cols)
    body = "".join(
        "<tr>" + "".join(f"<td>{esc(_fmt(r[c], 5))}</td>" for c in cols) + "</tr>"
        for r in result.peak_rows()
    )
    summary = "\n".join(esc(line) for line in summary_lines(result))
    banner = (
        '<p class="synthetic">SYNTHETIC EXAMPLE DATA — not an instrument measurement</p>'
        if result.dataset.get("synthetic")
        else ""
    )
    wh_img = (
        f'<img alt="Williamson–Hall plot" src="data:image/png;base64,{wh_png}">' if wh_png else ""
    )
    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{esc(APP_NAME)} — {esc(str(result.dataset.get("name")))}</title>
<style>
body{{font-family:-apple-system,"Segoe UI","Malgun Gothic","Apple SD Gothic Neo",sans-serif;
margin:24px;color:#1f2328}} table{{border-collapse:collapse;font-size:12px}}
td,th{{border:1px solid #d0d7de;padding:3px 6px;text-align:right}} th{{background:#f6f8fa}}
pre{{white-space:pre-wrap;font-size:12px;background:#f6f8fa;padding:12px}}
.synthetic{{background:#fff8c5;padding:8px;font-weight:bold}} img{{max-width:100%}}
</style></head><body>
<h1>{esc(APP_NAME)} report</h1>
<p>{esc(DISCLAIMER_EN)}<br>{esc(DISCLAIMER_KO)}</p>
{banner}
<h2>Pattern</h2><img alt="pattern with fit" src="data:image/png;base64,{pattern_png}">
<h2>Peak table</h2><table><tr>{head}</tr>{body}</table>
<h2>Williamson–Hall</h2>{wh_img}
<h2>Summary &amp; provenance</h2><pre>{summary}</pre>
</body></html>
"""
    p.write_text(doc, encoding="utf-8")
    return p
