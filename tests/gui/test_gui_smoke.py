"""Offscreen GUI smoke tests (QT_QPA_PLATFORM=offscreen). Real-display checks are manual."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytestmark = pytest.mark.gui

from PySide6.QtWidgets import QApplication  # noqa: E402

from xrd_analyzer.io import ReadOptions  # noqa: E402
from xrd_analyzer.ui.main_window import MainWindow  # noqa: E402


@pytest.fixture
def win(qtbot):
    QApplication.instance().setProperty("headless_tests", True)
    w = MainWindow("ko")
    qtbot.addWidget(w)
    w.show()
    return w


def _wait(qtbot, w, timeout=30000):
    qtbot.waitUntil(lambda: not w.busy, timeout=timeout)


def test_open_example_run_export(win, qtbot, tmp_path):
    win.open_example("nano_fcc")
    win.apply_and_run()
    assert win.busy  # runs in a worker thread, GUI stays responsive
    _wait(qtbot, win)
    item = win.current()
    assert item.result is not None and win.table.rowCount() == 6
    assert "SYNTHETIC" in win.summary.toPlainText()
    paths = win.export_to(str(tmp_path))
    assert any(p.suffix == ".pdf" for p in paths) and any(p.suffix == ".html" for p in paths)


def test_undo_redo_parameter_and_peak_edits(win, qtbot):
    win.open_example("si_like")
    win.apply_and_run()
    _wait(qtbot, win)
    assert win.table.rowCount() == 6
    win.add_peak_at(60.0)  # manual peak on empty background
    _wait(qtbot, win)
    assert win.current().recipe.peaks.manual_add_deg == [60.0]
    assert win.table.rowCount() == 7
    win.table.selectRow(0)
    win.remove_selected_peak()
    _wait(qtbot, win)
    assert win.table.rowCount() == 6
    win.undo_stack.undo()
    _wait(qtbot, win)
    assert win.table.rowCount() == 7
    win.undo_stack.undo()
    _wait(qtbot, win)
    assert win.current().recipe.peaks.manual_add_deg == []
    win.undo_stack.redo()
    _wait(qtbot, win)
    assert win.current().recipe.peaks.manual_add_deg == [60.0]


def test_missing_wavelength_is_reported_not_crashed(win, qtbot, si_csv):
    idx = win.open_file(str(si_csv), ask=False)
    assert idx is not None
    win.apply_and_run()
    _wait(qtbot, win)
    assert "wavelength is not set" in (win.current().error or "")
    assert "ERROR" in win.log_view.toPlainText()


def test_headerless_file_needs_confirmation(win, qtbot, tmp_path):
    p = tmp_path / "h.xy"
    p.write_text("\n".join(f"{20 + 0.01 * i:.2f} {i % 50 + 1}" for i in range(500)), "utf-8")
    assert win.open_file(str(p), ask=False) is None
    assert "axis quantity" in win.log_view.toPlainText()
    assert win.open_file(str(p), ReadOptions(axis_quantity="2theta", axis_unit="deg")) == 0


def test_batch_cancel_language_theme_project(win, qtbot, tmp_path):
    for k in ("si_like", "si_like_ka2"):
        win.open_example(k)
    win.run_batch_checked()
    _wait(qtbot, win)
    assert win.batch_stats and all(it.result is not None for it in win.items)
    win.open_example("nano_fcc")
    win.apply_and_run()
    win.toggle_pause()
    win.cancel()
    _wait(qtbot, win)
    assert "cancelled" in win.log_view.toPlainText() or win.current().result is not None
    win.set_language("en")
    assert win.m_file.title() == "&File"
    win.set_language("ko")
    assert win.m_file.title() == "파일"
    win.toggle_dark(True)
    win.toggle_dark(False)
    win.change_font(1)
    path = tmp_path / "p.xrdproj.json"
    win.save_project_to(str(path))
    w2 = MainWindow("en")
    qtbot.addWidget(w2)
    assert w2.open_project_from(str(path)) == []
    assert len(w2.items) == 3


def test_worker_signals_connected_before_start(qtbot):
    from xrd_analyzer.ui.workers import make_worker

    parent = MainWindow("en")
    qtbot.addWidget(parent)
    thread, worker = make_worker(parent, lambda progress, cancel: 42)
    assert not thread.isRunning()  # nothing runs until the receivers are attached
    with qtbot.waitSignal(worker.finished, timeout=5000) as sig:
        thread.start()
    assert sig.args == [42]
    qtbot.waitUntil(lambda: thread.isFinished(), timeout=5000)


def test_close_waits_for_running_worker(win, qtbot):
    import time

    from xrd_analyzer.core.fit import AnalysisCancelled

    def slow_job(progress, cancel):
        while not cancel():
            time.sleep(0.01)
        raise AnalysisCancelled("stopped")

    win._start(slow_job, lambda _out: None)
    assert win.busy
    assert win.close() is False  # close is deferred, the event loop is not blocked
    assert win.isVisible()
    qtbot.waitUntil(lambda: not win.isVisible(), timeout=10000)
    assert win.worker is None


def test_gui_smoke_command(qtbot, tmp_path):
    import json

    from xrd_analyzer.cli import main

    out = tmp_path / "gui.json"
    assert main(["gui-smoke", "--offscreen", "--out", str(out)]) == 0
    assert json.loads(out.read_text("utf-8"))["gui_smoke"] == "PASS"


def _real_style_xy(tmp_path):
    p = tmp_path / "scan_exported.xy"
    header = (
        '\'Id: "S" Anode: "Cu" Wavelength: "1.5406" Scantype: "2Theta (Coupled TwoTheta/Theta)" '
        'TimePerStep: "28.548" \n'
    )
    rows = "\n".join(f"{5 + 0.01 * i:.5f} {(70 + i % 9) / 28.548:.3f}" for i in range(300))
    p.write_text(header + rows + "\n", encoding="utf-8")
    return p


def test_import_dialog_never_defaults_to_counts(win, tmp_path):
    from xrd_analyzer.io import probe_file
    from xrd_analyzer.ui.dialogs import ImportDialog

    p = tmp_path / "plain.xy"
    p.write_text("\n".join(f"{20 + i * 0.1:.1f} {i}" for i in range(50)) + "\n", encoding="utf-8")
    dlg = ImportDialog(probe_file(p), "confirm", win)
    assert dlg.iunit.currentIndex() == -1
    dlg._accept()
    assert "intensity unit" in dlg.error.text() and not dlg.result()
    dlg.iunit.setCurrentIndex(dlg.iunit.findData("counts"))
    dlg._accept()
    assert dlg.options().intensity_unit == "counts" and dlg.result()


def test_import_dialog_blocks_counts_for_fractional_values(win, tmp_path):
    from xrd_analyzer.io import probe_file
    from xrd_analyzer.ui.dialogs import ImportDialog

    dlg = ImportDialog(probe_file(_real_style_xy(tmp_path)), "confirm", win)
    assert not dlg.iunit.model().item(dlg.iunit.findData("counts")).isEnabled()
    assert dlg.iunit.currentData() == "cps"  # the header and the values point to count rates
    assert dlg.options().intensity_unit == "cps"


def test_import_dialog_keeps_a_declared_unit_unless_the_values_rule_it_out(win, tmp_path):
    from xrd_analyzer.io import probe_file
    from xrd_analyzer.ui.dialogs import ImportDialog

    whole = tmp_path / "w.csv"
    whole.write_text("2theta_deg,counts\n" + "\n".join(f"{20 + i},{i}" for i in range(9)), "utf-8")
    assert ImportDialog(probe_file(whole), "m", win).iunit.currentData() == "counts"
    fractional = tmp_path / "f.csv"
    fractional.write_text(
        "2theta_deg,counts\n" + "\n".join(f"{20 + i},{i}.5" for i in range(9)), "utf-8"
    )
    assert ImportDialog(probe_file(fractional), "m", win).iunit.currentIndex() == -1


def test_real_style_export_finds_nothing_false_with_the_offered_unit(win, qtbot, tmp_path):
    from xrd_analyzer.io import probe_file
    from xrd_analyzer.ui.dialogs import ImportDialog

    p = _real_style_xy(tmp_path)
    dlg = ImportDialog(probe_file(p), "confirm", win)
    dlg._accept()
    assert win.open_file(str(p), dlg.options()) == 0
    assert win.current().pattern.intensity_unit == "cps"
    assert "file records wavelength 1.5406" in win.log_view.toPlainText()


def test_brml_opens_without_a_dialog(win, tmp_path):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parents[1] / "unit"))
    from test_bruker_brml import make_brml

    path = make_brml(tmp_path / "scan.brml")
    assert win.open_file(str(path), ask=False) == 0
    item = win.current()
    assert item.pattern.counts_like and item.pattern.parser == "bruker-brml"


def test_origin_folder_from_the_current_dataset(win, qtbot, tmp_path):
    win.open_example("si_like")
    with pytest.raises(ValueError, match="nothing analysed"):
        win.export_origin_to(str(tmp_path))
    win.apply_and_run()
    _wait(qtbot, win)
    bundle = win.export_origin_to(str(tmp_path))
    assert bundle.launcher.is_file() and (bundle.folder / "pattern_01.csv").is_file()
    assert "for Origin" in win.log_view.toPlainText()


def test_origin_folder_holds_every_checked_analysed_dataset(win, qtbot, tmp_path):
    for key in ("si_like", "nano_fcc"):
        win.open_example(key)
        win.apply_and_run()
        _wait(qtbot, win)
    bundle = win.export_origin_to(str(tmp_path))
    assert (bundle.folder / "pattern_02.csv").is_file()
    assert bundle.project.name == "xrd_analysis.opju"
