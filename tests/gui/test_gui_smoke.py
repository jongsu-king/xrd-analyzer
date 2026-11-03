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
