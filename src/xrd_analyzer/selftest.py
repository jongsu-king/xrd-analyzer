"""Built-in self-test for installed / frozen builds.

``xrd-analyzer selftest`` re-runs the three synthetic ground-truth examples, checks the key
results against their known answers, and exercises every export format (CSV, JSON, PNG,
SVG, HTML, PDF), so a packaged application can verify itself on the target machine.
``xrd-analyzer gui-smoke`` opens the main window, analyses an example in the background
worker and closes again (use ``--offscreen`` on machines without a display).
"""

from __future__ import annotations

import json
import os
import platform
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any

from . import __version__


def _environment() -> dict[str, Any]:
    import matplotlib
    import numpy
    import scipy

    return {
        "version": __version__,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "numpy": numpy.__version__,
        "scipy": scipy.__version__,
        "matplotlib": matplotlib.__version__,
    }


def run_selftest() -> dict[str, Any]:
    from .core.analysis import run_analysis
    from .core.synthetic import make_example
    from .reports.exports import export_all
    from .reports.origin import write_origin_bundle

    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    try:
        for key in ("si_like", "si_like_ka2", "nano_fcc"):
            ex = make_example(key)
            res = run_analysis(ex.pattern, ex.recipe)
            fitted = sorted(p.fit["center_deg"] for p in res.peaks)
            truth = [p["two_theta_deg"] for p in ex.truth["peaks"]]
            ok = len(fitted) == len(truth) and all(
                abs(f - t) <= max(0.005, 0.01 * p["fwhm_deg"])
                for f, t, p in zip(fitted, truth, ex.truth["peaks"], strict=False)
            )
            worst = max((abs(f - t) for f, t in zip(fitted, truth, strict=False)), default=-1)
            check(
                f"{key}: peak positions",
                ok,
                f"{len(fitted)}/{len(truth)} peaks, max |Δ2θ| = {worst:.5f}°",
            )
            if key == "nano_fcc":
                wh = res.williamson_hall
                d = wh.size.value if wh and wh.size else None
                e = wh.strain.value if wh and wh.strain else None
                ok = (
                    d is not None
                    and abs(d - ex.truth["D_nm"]) / ex.truth["D_nm"] < 0.05
                    and e is not None
                    and abs(e - ex.truth["strain"]) / ex.truth["strain"] < 0.15
                )
                check(
                    "nano_fcc: Williamson–Hall size/strain",
                    ok,
                    f"D = {d} nm (truth {ex.truth['D_nm']}), ε = {e} (truth {ex.truth['strain']})",
                )
            if key == "si_like":
                ok = all(p.scherrer.status == "NOT_CALCULABLE" for p in res.peaks if p.scherrer)
                check(
                    "si_like: Scherrer refused without instrumental broadening",
                    ok,
                    "all NOT_CALCULABLE" if ok else "a size was reported without β_instrument",
                )
                with tempfile.TemporaryDirectory() as tmp:
                    written = export_all(res, tmp)
                    suffixes = sorted({p.suffix for p in written})
                    sizes_ok = all(p.stat().st_size > 0 for p in written)
                    check(
                        "exports (CSV, JSON, PNG, SVG, HTML, PDF)",
                        suffixes == [".csv", ".html", ".json", ".pdf", ".png", ".svg"] and sizes_ok,
                        ", ".join(suffixes),
                    )
                    bundle = write_origin_bundle([res], Path(tmp) / "origin")
                    check(
                        "Origin folder (data, script, launcher)",
                        bundle.script.stat().st_size > 0 and bundle.launcher.stat().st_size > 0,
                        bundle.script.name,
                    )
    except Exception as exc:  # report, never crash the self-test
        check("unexpected error", False, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    ok = bool(checks) and all(c["ok"] for c in checks)
    return {
        "selftest": "PASS" if ok else "FAIL",
        "ok": ok,
        "checks": checks,
        "environment": _environment(),
    }


def run_gui_smoke(offscreen: bool = False, timeout_s: float = 120.0) -> dict[str, Any]:
    if offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    detail = ""
    ok = False
    try:
        from PySide6.QtWidgets import QApplication

        from .ui.main_window import MainWindow
        from .ui.theme import apply_theme

        app = QApplication.instance() or QApplication(sys.argv[:1])
        assert isinstance(app, QApplication)
        app.setProperty("headless_tests", True)
        apply_theme(app, dark=False)
        win = MainWindow("en")
        win.show()
        win.open_example("nano_fcc")
        win.apply_and_run()
        t0 = time.monotonic()
        while win.busy and time.monotonic() - t0 < timeout_s:
            app.processEvents()
            time.sleep(0.01)
        item = win.current()
        rows = win.table.rowCount()
        ok = (not win.busy) and item is not None and item.result is not None and rows == 6
        detail = f"busy={win.busy}, result={'yes' if item and item.result else 'no'}, rows={rows}"
        win.close()
        app.processEvents()
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
    return {
        "gui_smoke": "PASS" if ok else "FAIL",
        "ok": ok,
        "detail": detail,
        "environment": _environment(),
    }


def write_report(report: dict[str, Any], out: str | None) -> None:
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if out:
        Path(out).write_text(text, encoding="utf-8")
    try:
        print(text)
    except (OSError, ValueError):  # windowed builds may have no console
        pass
