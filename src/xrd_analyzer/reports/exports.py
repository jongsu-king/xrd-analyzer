"""CSV / JSON / figure exports of an :class:`AnalysisResult` (reports: see report.py)."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
from matplotlib.figure import Figure

from .. import DISCLAIMER_EN
from ..core.analysis import AnalysisResult
from .figures import PlotSettings, draw_result, save_figure
from .report import write_html_report, write_pdf_report

PEAK_COLUMNS = [
    ("peak_id", ""),
    ("source", ""),
    ("profile", ""),
    ("two_theta_deg", "deg"),
    ("two_theta_err_deg", "deg"),
    ("d_A", "Å"),
    ("d_err_A", "Å"),
    ("fwhm_deg", "deg 2θ"),
    ("fwhm_err_deg", "deg 2θ"),
    ("eta", ""),
    ("height", "intensity"),
    ("height_err", "intensity"),
    ("area", "intensity·deg"),
    ("area_err", "intensity·deg"),
    ("scherrer_nm", "nm"),
    ("scherrer_err_nm", "nm"),
    ("scherrer_status", ""),
    ("scherrer_reason", ""),
    ("significance_sigma", "σ"),
    ("flags", ""),
]


def _clean(v: Any) -> Any:
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, np.floating):
        return _clean(float(v))
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.ndarray):
        return [_clean(i) for i in v.tolist()]
    if isinstance(v, dict):
        return {k: _clean(i) for k, i in v.items()}
    if isinstance(v, list | tuple):
        return [_clean(i) for i in v]
    return v


def write_peak_table_csv(result: AnalysisResult, path: str | Path) -> Path:
    p = Path(path)
    with p.open("w", newline="", encoding="utf-8") as fh:
        fh.write(f"# {DISCLAIMER_EN}\n")
        ds = result.dataset
        fh.write(f"# dataset: {ds.get('name')}  sha256: {ds.get('sha256')}\n")
        if result.dataset.get("synthetic"):
            fh.write("# SYNTHETIC EXAMPLE DATA — not an instrument measurement\n")
        fh.write(f"# wavelength_A: {result.recipe['preprocess']['wavelength_A']}\n")
        for m in result.processed.uncertainty_model:
            fh.write(f"# uncertainty model: {m}\n")
        if result.fit.doublet is not None:
            total = 1.0 + result.fit.doublet[2]
            fh.write(
                "# doublet fit: height and area are Kα1 only; "
                f"whole reflection = {total:g} × area\n"
            )
        w = csv.writer(fh)
        w.writerow([f"{c} [{u}]" if u else c for c, u in PEAK_COLUMNS])
        for row in result.peak_rows():
            w.writerow(["" if _clean(row[c]) is None else row[c] for c, _ in PEAK_COLUMNS])
    return p


def write_processed_csv(result: AnalysisResult, path: str | Path) -> Path:
    p = Path(path)
    table = result.processed.table()
    names = list(table)
    data = np.column_stack([table[n] for n in names])
    with p.open("w", newline="", encoding="utf-8") as fh:
        fh.write(f"# processed data — raw file unchanged; sha256 {result.dataset.get('sha256')}\n")
        w = csv.writer(fh)
        w.writerow(names)
        for r in data:
            w.writerow([f"{v:.8g}" for v in r])
    return p


def write_result_json(
    result: AnalysisResult, path: str | Path, include_arrays: bool = False
) -> Path:
    p = Path(path)
    p.write_text(
        json.dumps(_clean(result.to_dict(include_arrays)), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return p


def export_all(
    result: AnalysisResult,
    out_dir: str | Path,
    settings: PlotSettings | None = None,
    pdf: bool = True,
) -> list[Path]:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    stem = result.dataset.get("name") or "pattern"
    written = [
        write_peak_table_csv(result, d / f"{stem}_peaks.csv"),
        write_processed_csv(result, d / f"{stem}_processed.csv"),
        write_result_json(result, d / f"{stem}_result.json"),
    ]
    fig = Figure(figsize=(8, 5.5))
    draw_result(fig, result, settings)
    for ext in ("png", "svg"):
        written.append(save_figure(fig, d / f"{stem}_pattern.{ext}"))
    written.append(write_html_report(result, d / f"{stem}_report.html", settings))
    if pdf:
        written.append(write_pdf_report(result, d / f"{stem}_report.pdf", settings))
    return written
