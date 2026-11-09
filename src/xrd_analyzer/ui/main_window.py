"""Main window: datasets (left), plots (centre), parameters/results (right), log (bottom)."""

from __future__ import annotations

import csv
import datetime as _dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QDragEnterEvent,
    QDropEvent,
    QKeySequence,
    QUndoCommand,
    QUndoStack,
)
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QFileDialog,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QWidget,
)

from .. import APP_NAME, DISCLAIMER_EN, DISCLAIMER_KO, __version__
from ..core.analysis import AnalysisResult, batch_peak_statistics, run_analysis, run_batch
from ..core.reference import ReferencePattern, load_reference_csv, reference_from_cubic
from ..core.synthetic import EXAMPLES, make_example
from ..io import AxisConfirmationRequired, ReadOptions, XRDFormatError, probe_file, read_pattern
from ..models.dataset import XRDPattern
from ..models.recipe import AnalysisRecipe
from ..project import (
    Project,
    ProjectError,
    entry_for,
    load_project,
    resolve_background,
    resolve_entry,
    save_project,
)
from ..reports.exports import export_all
from ..reports.figures import PlotSettings, draw_overlay, draw_result, draw_wh, save_figure
from .dialogs import CubicReferenceDialog, ImportDialog, PlotSettingsDialog
from .i18n import Translator
from .params import ParamPanel
from .plots import PlotPanel
from .theme import apply_theme
from .workers import Worker, make_worker

DATA_FILTER = "XRD data (*.csv *.txt *.xy *.dat *.tsv);;All files (*)"


@dataclass
class DatasetItem:
    pattern: XRDPattern
    recipe: AnalysisRecipe
    read_options: ReadOptions | None = None
    example_key: str | None = None
    background: XRDPattern | None = None
    result: AnalysisResult | None = None
    error: str | None = None


class SetRecipeCommand(QUndoCommand):
    """Non-destructive edit: the raw data never change, only the recipe (incl. peak edits)."""

    def __init__(
        self, win: MainWindow, index: int, old: dict[str, Any], new: dict[str, Any], text: str
    ) -> None:
        super().__init__(text)
        self.win, self.index, self.old, self.new = win, index, old, new

    def redo(self) -> None:
        self.win.apply_recipe(self.index, AnalysisRecipe.from_dict(self.new))

    def undo(self) -> None:
        self.win.apply_recipe(self.index, AnalysisRecipe.from_dict(self.old))


def update_app(selfupdate):
    """Description of this program for the automatic updater."""
    return selfupdate.App(repo="jongsu-king/xrd-analyzer", name=APP_NAME, version=__version__,
                          env_prefix="XRD_ANALYZER", pip_name="xrd-analyzer")


class MainWindow(QMainWindow):
    def __init__(self, lang: str = "ko") -> None:
        super().__init__()
        self.tr_ = Translator(lang)
        self.items: list[DatasetItem] = []
        self.references: list[ReferencePattern] = []
        self.plot_settings = PlotSettings()
        self.dark = False
        self.normalise_overlay = True
        self.undo_stack = QUndoStack(self)
        self.thread: Any = None
        self.worker: Worker | None = None
        self._pending_run = False
        self._close_requested = False
        self.batch_stats: list[dict[str, Any]] = []
        self.setAcceptDrops(True)
        self.resize(1500, 900)
        self._build()
        self.retranslate()
        self.log(f"{APP_NAME} {__version__} — {DISCLAIMER_EN}")

    # ------------------------------------------------------------------------- building
    def _build(self) -> None:
        self.data_list = QListWidget()
        self.data_list.currentRowChanged.connect(self._on_select)
        self.data_list.itemChanged.connect(lambda _i: self.refresh_overlay())
        self.dock_data = self._dock(self.data_list, Qt.DockWidgetArea.LeftDockWidgetArea)

        self.plot_tabs = QTabWidget()
        self.pattern_plot = PlotPanel()
        self.overlay_plot = PlotPanel()
        self.wh_plot = PlotPanel()
        for w in (self.pattern_plot, self.overlay_plot, self.wh_plot):
            self.plot_tabs.addTab(w, "")
        self.setCentralWidget(self.plot_tabs)

        self.params = ParamPanel(self.tr_, self.apply_and_run)
        self.params.add_cubic_btn.clicked.connect(self.add_cubic_reference)
        self.params.load_ref_btn.clicked.connect(self.load_reference)
        self.params.remove_ref_btn.clicked.connect(self.remove_reference)
        self.dock_params = self._dock(self.params, Qt.DockWidgetArea.RightDockWidgetArea)

        self.table = QTableWidget(0, 8)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.table)
        split.addWidget(self.summary)
        self.dock_results = self._dock(split, Qt.DockWidgetArea.RightDockWidgetArea)
        self.tabifyDockWidget(self.dock_params, self.dock_results)
        self.dock_params.raise_()

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        self.dock_log = self._dock(self.log_view, Qt.DockWidgetArea.BottomDockWidgetArea)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setMaximumWidth(260)
        self.cancel_btn = QPushButton()
        self.cancel_btn.clicked.connect(self.cancel)
        self.pause_btn = QPushButton()
        self.pause_btn.clicked.connect(self.toggle_pause)
        for w in (self.progress, self.pause_btn, self.cancel_btn):
            self.statusBar().addPermanentWidget(w)
        self._set_busy(False)
        self._build_actions()

    def _dock(self, w: QWidget, area: Qt.DockWidgetArea) -> QDockWidget:
        d = QDockWidget(self)
        d.setObjectName(f"dock{id(w)}")
        d.setWidget(w)
        self.addDockWidget(area, d)
        return d

    def _act(
        self,
        key: str,
        slot: Any,
        shortcut: QKeySequence | str | None = None,
        checkable: bool = False,
    ) -> QAction:
        a = QAction(self)
        a.setData(key)
        if shortcut is not None:
            a.setShortcut(QKeySequence(shortcut))
        a.setCheckable(checkable)
        a.triggered.connect(slot)
        self._actions.append(a)
        return a

    def _build_actions(self) -> None:
        self._actions: list[QAction] = []
        mb = self.menuBar()
        self.m_file = mb.addMenu("")
        self.m_file.addAction(
            self._act("act.open", self.open_dialog, QKeySequence.StandardKey.Open)
        )
        self.m_examples = self.m_file.addMenu("")
        for key in EXAMPLES:
            a = QAction(make_example(key).title, self)
            a.triggered.connect(lambda _c=False, k=key: self.open_example(k))
            self.m_examples.addAction(a)
        self.m_file.addAction(self._act("act.background", self.load_background))
        self.m_file.addAction(self._act("act.clear_background", self.clear_background))
        self.m_file.addSeparator()
        self.m_file.addAction(self._act("act.open_project", self.open_project_dialog))
        self.m_file.addAction(
            self._act("act.save_project", self.save_project_dialog, QKeySequence.StandardKey.Save)
        )
        self.m_file.addAction(self._act("act.load_recipe", self.load_recipe_dialog))
        self.m_file.addAction(self._act("act.save_recipe", self.save_recipe_dialog))
        self.m_file.addSeparator()
        self.m_file.addAction(self._act("act.export", self.export_dialog, "Ctrl+E"))
        self.m_file.addAction(self._act("act.export_fig", self.export_figure_dialog))
        self.m_file.addAction(self._act("act.export_batch", self.export_batch_dialog))
        self.m_file.addSeparator()
        self.m_file.addAction(self._act("act.quit", self.close, QKeySequence.StandardKey.Quit))

        self.m_edit = mb.addMenu("")
        u = self._act("act.undo", self.undo_stack.undo, QKeySequence.StandardKey.Undo)
        r = self._act("act.redo", self.undo_stack.redo, QKeySequence.StandardKey.Redo)
        self.undo_stack.canUndoChanged.connect(u.setEnabled)
        self.undo_stack.canRedoChanged.connect(r.setEnabled)
        u.setEnabled(False)
        r.setEnabled(False)
        self.add_mode = self._act("act.add_peak", self._toggle_add_mode, "A", checkable=True)
        for a in (
            u,
            r,
            self.add_mode,
            self._act(
                "act.remove_peak", self.remove_selected_peak, QKeySequence.StandardKey.Delete
            ),
            self._act("act.clear_edits", self.clear_peak_edits),
        ):
            self.m_edit.addAction(a)

        self.m_analysis = mb.addMenu("")
        self.run_act = self._act("act.run", self.apply_and_run, "F5")
        self.batch_act = self._act("act.batch", self.run_batch_checked, "Shift+F5")
        for a in (
            self.run_act,
            self.batch_act,
            self._act("act.cancel", self.cancel, "Esc"),
            self._act("act.pause", self.toggle_pause, "Ctrl+P"),
        ):
            self.m_analysis.addAction(a)

        self.m_view = mb.addMenu("")
        self.dark_act = self._act("act.dark", self.toggle_dark, "Ctrl+T", checkable=True)
        self.m_view.addAction(self.dark_act)
        grp = QActionGroup(self)
        for code in ("ko", "en"):
            a = self._act(
                f"act.lang_{code}", lambda _c=False, c=code: self.set_language(c), checkable=True
            )
            a.setChecked(self.tr_.lang == code)
            grp.addAction(a)
            self.m_view.addAction(a)
        self.m_view.addSeparator()
        norm = self._act("act.normalise", self._toggle_normalise, checkable=True)
        norm.setChecked(True)
        for a in (
            self._act("act.plot_settings", self.plot_settings_dialog),
            norm,
            self._act("act.font_up", lambda: self.change_font(1), "Ctrl+="),
            self._act("act.font_down", lambda: self.change_font(-1), "Ctrl+-"),
        ):
            self.m_view.addAction(a)
        self.m_view.addSeparator()
        for d in (self.dock_data, self.dock_params, self.dock_results, self.dock_log):
            self.m_view.addAction(d.toggleViewAction())

        self.m_help = mb.addMenu("")
        self.m_help.addAction(self._act("act.about", self.about))
        self.m_help.addSeparator()
        self._setup_updates()

    def _setup_updates(self) -> None:
        """Help menu: check for updates and choose the automatic-update mode."""
        try:
            from .. import selfupdate

            self.updater = selfupdate.Updater(update_app(selfupdate))
            selfupdate.attach_qt(self, self.updater, self.m_help, lang=lambda: self.tr_.lang)
        except Exception as exc:  # noqa: BLE001 - updates must never stop the program
            self.updater = None
            print(f"update support unavailable: {exc}")

    def retranslate(self) -> None:
        t = self.tr_
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        for m, key in (
            (self.m_file, "menu.file"),
            (self.m_edit, "menu.edit"),
            (self.m_analysis, "menu.analysis"),
            (self.m_view, "menu.view"),
            (self.m_help, "menu.help"),
            (self.m_examples, "act.examples"),
        ):
            m.setTitle(t(key))
        for a in self._actions:
            a.setText(t(str(a.data())))
        for d, key in (
            (self.dock_data, "dock.datasets"),
            (self.dock_params, "dock.params"),
            (self.dock_results, "dock.results"),
            (self.dock_log, "dock.log"),
        ):
            d.setWindowTitle(t(key))
        for i, key in enumerate(("tab.pattern", "tab.overlay", "tab.wh")):
            self.plot_tabs.setTabText(i, t(key))
        self.cancel_btn.setText(t("act.cancel"))
        self.pause_btn.setText(t("act.pause"))
        self.table.setHorizontalHeaderLabels(
            [
                t(k)
                for k in (
                    "col.id",
                    "col.src",
                    "col.tt",
                    "col.d",
                    "col.fwhm",
                    "col.area",
                    "col.D",
                    "col.flags",
                )
            ]
        )
        self.params.tr = t
        self.params.retranslate()

    # -------------------------------------------------------------------------- helpers
    def log(self, msg: str) -> None:
        ts = _dt.datetime.now().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{ts}] {msg}")

    def error(self, title: str, msg: str) -> None:
        self.log(f"ERROR: {title}: {msg}")
        if not QApplication.instance().property("headless_tests"):
            QMessageBox.warning(self, title, msg)

    @property
    def busy(self) -> bool:
        return self.worker is not None

    def current_index(self) -> int:
        return self.data_list.currentRow()

    def current(self) -> DatasetItem | None:
        i = self.current_index()
        return self.items[i] if 0 <= i < len(self.items) else None

    def _set_busy(self, busy: bool) -> None:
        self.progress.setVisible(busy)
        self.cancel_btn.setVisible(busy)
        self.pause_btn.setVisible(busy)
        if not busy:
            self.progress.setValue(0)

    # ------------------------------------------------------------------------- datasets
    def add_item(self, item: DatasetItem) -> int:
        self.items.append(item)
        label = item.pattern.name + ("  [SYNTHETIC]" if item.pattern.synthetic else "")
        li = QListWidgetItem(label)
        li.setFlags(li.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        li.setCheckState(Qt.CheckState.Checked)
        li.setToolTip(
            f"{item.pattern.source_path or 'in memory'}\nSHA-256 {item.pattern.sha256}"
            f"\n{item.pattern.parser} [{item.pattern.format_status}]"
            f"\nflags: {', '.join(item.pattern.quality_flags) or 'none'}"
        )
        self.data_list.addItem(li)
        self.data_list.setCurrentRow(len(self.items) - 1)
        self.refresh_overlay()
        return len(self.items) - 1

    def open_example(self, key: str) -> int:
        ex = make_example(key)
        self.log(f"Opened {ex.title} — {self.tr_('msg.synthetic')}. {ex.description}")
        self.log(
            "Example recipe sets the generator's wavelength (Cu Kα1) — for real data you "
            "must select the wavelength yourself."
        )
        return self.add_item(DatasetItem(ex.pattern, ex.recipe.copy(), example_key=key))

    def open_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, self.tr_("act.open"), "", DATA_FILTER)
        self.open_files(paths)

    def open_files(self, paths: list[str]) -> None:
        for p in paths:
            self.open_file(p)

    def open_file(
        self, path: str, options: ReadOptions | None = None, ask: bool = True
    ) -> int | None:
        try:
            pat = read_pattern(path, options)
        except AxisConfirmationRequired as exc:
            if not ask:
                self.error(Path(path).name, str(exc))
                return None
            probe = exc.probe or probe_file(path)
            dlg = ImportDialog(probe, str(exc), self)
            if dlg.exec() != ImportDialog.DialogCode.Accepted:
                self.log(f"Import of {Path(path).name} cancelled by user")
                return None
            return self.open_file(path, dlg.options(), ask=True)
        except (XRDFormatError, OSError) as exc:
            self.error(Path(path).name, str(exc))
            return None
        self.log(
            f"Opened {path} [{pat.parser}, {pat.format_status}] {pat.n_points} points, "
            f"SHA-256 {pat.sha256}"
        )
        if pat.quality_flags:
            self.log(f"  quality flags: {', '.join(pat.quality_flags)}")
        recipe = self.current().recipe.copy() if self.current() else AnalysisRecipe()
        recipe.peaks.manual_add_deg, recipe.peaks.manual_remove_deg = [], []
        return self.add_item(DatasetItem(pat, recipe, read_options=options))

    def dragEnterEvent(self, e: QDragEnterEvent) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e: QDropEvent) -> None:
        self.open_files([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])

    def _on_select(self, row: int) -> None:
        item = self.current()
        if item is None:
            return
        self.params.set_recipe(item.recipe)
        self.refresh_views()

    def checked_indices(self) -> list[int]:
        out = [
            i
            for i in range(self.data_list.count())
            if self.data_list.item(i).checkState() == Qt.CheckState.Checked
        ]
        return out or list(range(len(self.items)))

    def load_background(self) -> None:
        item = self.current()
        if item is None:
            return self.error("background", self.tr_("msg.no_data"))
        path, _ = QFileDialog.getOpenFileName(self, self.tr_("act.background"), "", DATA_FILTER)
        if not path:
            return None
        try:
            item.background = read_pattern(path, item.read_options)
        except (XRDFormatError, OSError) as exc:
            return self.error("background", str(exc))
        self.log(
            f"Measured background for {item.pattern.name}: {path} "
            f"(SHA-256 {item.background.sha256})"
        )
        return None

    def clear_background(self) -> None:
        item = self.current()
        if item is not None and item.background is not None:
            item.background = None
            self.log(f"Measured background cleared for {item.pattern.name}")

    # ------------------------------------------------------------------- recipe / undo
    def push_recipe(self, new: AnalysisRecipe, text: str) -> None:
        i = self.current_index()
        item = self.current()
        if item is None:
            return
        old = item.recipe.to_dict()
        if new.to_dict() == old:
            self.run_current()
            return
        self.undo_stack.push(SetRecipeCommand(self, i, old, new.to_dict(), text))

    def apply_recipe(self, index: int, recipe: AnalysisRecipe) -> None:
        if not 0 <= index < len(self.items):
            return
        self.items[index].recipe = recipe
        if index != self.current_index():
            self.data_list.setCurrentRow(index)
        self.params.set_recipe(recipe)
        self.run_current()

    def apply_and_run(self) -> None:
        item = self.current()
        if item is None:
            return self.error("run", self.tr_("msg.no_data"))
        self.push_recipe(self.params.recipe(item.recipe), "edit parameters")
        return None

    def _toggle_add_mode(self, on: bool) -> None:
        self.pattern_plot.set_click_handler(self.add_peak_at if on else None)
        self.log("Add-peak mode " + ("ON: click the pattern to add a peak" if on else "OFF"))

    def add_peak_at(self, two_theta: float) -> None:
        item = self.current()
        if item is None:
            return
        r = item.recipe.copy()
        r.peaks.manual_add_deg = [*r.peaks.manual_add_deg, round(two_theta, 4)]
        self.push_recipe(r, f"add peak {two_theta:.3f}°")

    def remove_selected_peak(self) -> None:
        item = self.current()
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if item is None or item.result is None or not rows:
            return
        r = item.recipe.copy()
        for row in rows:
            rep = item.result.peaks[row]
            # the selected row's peak_id identifies its seed candidate exactly
            pos = item.result.removal_position(rep.peak_id)
            if rep.fit["source"] == "manual" and r.peaks.manual_add_deg:
                nearest = min(r.peaks.manual_add_deg, key=lambda v: abs(v - pos))
                r.peaks.manual_add_deg = [v for v in r.peaks.manual_add_deg if v != nearest]
            else:
                r.peaks.manual_remove_deg = [*r.peaks.manual_remove_deg, round(pos, 4)]
        self.push_recipe(r, "remove peak(s)")

    def clear_peak_edits(self) -> None:
        item = self.current()
        if item is None:
            return
        r = item.recipe.copy()
        r.peaks.manual_add_deg, r.peaks.manual_remove_deg = [], []
        self.push_recipe(r, "clear peak edits")

    # ------------------------------------------------------------------------ analysis
    def run_current(self) -> None:
        item = self.current()
        if item is None:
            return
        if self.busy:
            self._pending_run = True
            self.worker.request_cancel()  # type: ignore[union-attr]
            return
        recipe, pattern, bg, refs = (
            item.recipe.copy(),
            item.pattern,
            item.background,
            list(self.references),
        )
        index = self.current_index()

        def job(progress: Any, cancel: Any) -> AnalysisResult:
            return run_analysis(pattern, recipe, bg, refs, progress, cancel)

        self.log(f"Analysing {pattern.name} …")
        self._start(job, lambda res: self._single_done(index, res))

    def run_batch_checked(self) -> None:
        if self.busy:
            return self.error("batch", self.tr_("msg.busy"))
        base = self.current()
        if base is None:
            return self.error("batch", self.tr_("msg.no_data"))
        recipe = self.params.recipe(base.recipe)
        recipe.peaks.manual_add_deg, recipe.peaks.manual_remove_deg = [], []
        idx = self.checked_indices()
        pats = [self.items[i].pattern for i in idx]
        bgs = [self.items[i].background for i in idx]  # each dataset keeps its own background
        refs = list(self.references)

        def job(progress: Any, cancel: Any) -> Any:
            return run_batch(pats, recipe, refs, progress, cancel, backgrounds=bgs)

        self.log(
            f"Batch analysis of {len(idx)} dataset(s) with the current recipe "
            "(manual peak edits are not transferred)"
        )
        self._start(job, lambda items: self._batch_done(idx, recipe, items))
        return None

    def _start(self, job: Any, on_done: Any) -> None:
        # Signals go to bound methods with an explicit queued connection: a lambda receiver
        # has no thread affinity and would run in the worker thread (touching widgets there).
        self._on_done = on_done
        self.thread, self.worker = make_worker(self, job)
        q = Qt.ConnectionType.QueuedConnection
        self.worker.progress.connect(self._on_progress, q)
        self.worker.finished.connect(self._dispatch_done, q)
        self.worker.failed.connect(self._on_failed, q)
        self.worker.cancelled.connect(self._on_cancelled, q)
        self.thread.finished.connect(self._on_thread_finished, q)
        self._set_busy(True)
        self.thread.start()  # only after every receiver is connected

    def _dispatch_done(self, out: Any) -> None:
        self._on_done(out)

    def _on_progress(self, frac: float, msg: str) -> None:
        self.progress.setValue(int(frac * 100))
        self.statusBar().showMessage(msg)

    def _single_done(self, index: int, res: AnalysisResult) -> None:
        item = self.items[index]
        item.result, item.error = res, None
        self.log(
            f"{item.pattern.name}: {len(res.peaks)} peak(s) fitted; "
            f"W–H {res.williamson_hall.status if res.williamson_hall else '—'}"
        )
        for w in res.warnings:
            self.log(f"  warning: {w}")
        if index == self.current_index():
            self.refresh_views()

    def _batch_done(self, idx: list[int], recipe: AnalysisRecipe, out: list[Any]) -> None:
        ok = []
        for i, bi in zip(idx, out, strict=True):
            item = self.items[i]
            if bi.result is not None:
                item.recipe, item.result, item.error = recipe.copy(), bi.result, None
                ok.append(bi.result)
            else:
                item.error = bi.error
                self.log(f"  FAILED {bi.name}: {bi.error}")
        self.batch_stats = batch_peak_statistics(ok, recipe.reference.tolerance_deg)
        self.log(
            f"Batch finished: {len(ok)}/{len(out)} succeeded; "
            f"{len(self.batch_stats)} peak group(s) in batch statistics"
        )
        self.refresh_views()

    def _on_failed(self, msg: str) -> None:
        self.error("analysis", msg.splitlines()[0])
        self.log(msg)
        item = self.current()
        if item is not None:
            item.error = msg.splitlines()[0]
            self.refresh_views()

    def _on_cancelled(self) -> None:
        self.log("Analysis cancelled")

    def _on_thread_finished(self) -> None:
        self.worker = None
        self.thread = None
        self._set_busy(False)
        self.statusBar().clearMessage()
        if self._close_requested:
            self.close()
            return
        if self._pending_run:
            self._pending_run = False
            self.run_current()

    def cancel(self) -> None:
        if self.worker is not None:
            self._pending_run = False
            self.worker.request_cancel()

    def toggle_pause(self) -> None:
        if self.worker is not None:
            paused = self.worker.toggle_pause()
            self.log("Paused" if paused else "Resumed")

    # --------------------------------------------------------------------------- views
    def refresh_views(self) -> None:
        item = self.current()
        fig = self.pattern_plot.figure
        if item is None:
            fig.clear()
        elif item.result is not None:
            draw_result(fig, item.result, self._ps())
        else:
            draw_overlay(
                fig,
                [(item.pattern.name, item.pattern.two_theta_deg, item.pattern.intensity)],
                PlotSettings.from_dict({**self._ps().to_dict(), "title": item.pattern.name}),
            )
        self.pattern_plot.redraw()
        if item is not None and item.result is not None:
            draw_wh(self.wh_plot.figure, item.result, self._ps())
        else:
            self.wh_plot.figure.clear()
        self.wh_plot.redraw()
        self._fill_table(item)
        self._fill_summary(item)
        self.refresh_overlay()

    def refresh_overlay(self) -> None:
        data = [
            (
                self.items[i].pattern.name,
                self.items[i].pattern.two_theta_deg,
                self.items[i].pattern.intensity,
            )
            for i in self.checked_indices()
            if i < len(self.items)
        ]
        draw_overlay(self.overlay_plot.figure, data, self._ps(), self.normalise_overlay)
        self.overlay_plot.redraw()

    def _ps(self) -> PlotSettings:
        return PlotSettings.from_dict({**self.plot_settings.to_dict(), "dark": self.dark})

    def _fill_table(self, item: DatasetItem | None) -> None:
        rows = item.result.peak_rows() if item and item.result else []
        self.table.setRowCount(len(rows))

        def f(v: Any, nd: int) -> str:
            return "—" if v is None or v != v else f"{v:.{nd}f}"

        for r, row in enumerate(rows):
            d = (
                f"{f(row['scherrer_nm'], 2)} ± {f(row['scherrer_err_nm'], 2)}"
                if row["scherrer_nm"] is not None
                else row["scherrer_status"]
            )
            vals = [
                str(row["peak_id"]),
                row["source"],
                f"{f(row['two_theta_deg'], 4)} ± {f(row['two_theta_err_deg'], 4)}",
                f"{f(row['d_A'], 5)} ± {f(row['d_err_A'], 5)}",
                f"{f(row['fwhm_deg'], 4)} ± {f(row['fwhm_err_deg'], 4)}",
                f(row["area"], 1),
                d,
                row["flags"],
            ]
            for c, v in enumerate(vals):
                cell = QTableWidgetItem(v)
                if c == 6 and row["scherrer_reason"]:
                    cell.setToolTip(row["scherrer_reason"])
                self.table.setItem(r, c, cell)
        self.table.resizeColumnsToContents()

    def _fill_summary(self, item: DatasetItem | None) -> None:
        from ..reports.report import summary_lines

        lines: list[str] = []
        if item is None:
            lines.append(self.tr_("msg.no_data"))
        else:
            if item.pattern.synthetic:
                lines.append("*** " + self.tr_("msg.synthetic") + " ***")
            if item.error:
                lines.append(f"ERROR: {item.error}")
            if item.result is not None:
                lines += summary_lines(item.result)
            else:
                lines.append("Not analysed yet — set the wavelength and press F5.")
        if self.batch_stats:
            lines += ["", "Batch statistics (N = patterns contributing):"]
            for b in self.batch_stats:
                sd = b["two_theta_std_deg"]
                lines.append(
                    f"  2θ {b['two_theta_mean_deg']:.4f}° "
                    f"(sd {'—' if sd is None else f'{sd:.4f}'}), "
                    f"d {b['d_mean_A']:.5f} Å, FWHM {b['fwhm_mean_deg']:.4f}°, "
                    f"N={b['n_patterns']}"
                )
        self.summary.setPlainText("\n".join(lines))

    # ------------------------------------------------------------------ references
    def add_cubic_reference(self) -> None:
        dlg = CubicReferenceDialog(self)
        if dlg.exec() != CubicReferenceDialog.DialogCode.Accepted:
            return
        item = self.current()
        wl = (item.recipe.preprocess.wavelength_A if item else None) or 1.5405929
        ref = reference_from_cubic(dlg.name.text(), dlg.a.value(), dlg.centering.currentData(), wl)
        self._add_reference(ref)

    def _add_reference(self, ref: ReferencePattern) -> None:
        self.references.append(ref)
        self.params.ref_list.addItem(f"{ref.name} — {ref.source}")
        self.log(f"Reference added: {ref.name} ({ref.source}; license: {ref.license_note})")

    def load_reference(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, self.tr_("btn.load_ref"), "", "Reference CSV (*.csv);;All files (*)"
        )
        if not path:
            return
        try:
            self._add_reference(load_reference_csv(path))
        except (ValueError, OSError) as exc:
            self.error("reference", str(exc))

    def remove_reference(self) -> None:
        row = self.params.ref_list.currentRow()
        if 0 <= row < len(self.references):
            ref = self.references.pop(row)
            self.params.ref_list.takeItem(row)
            self.log(f"Reference removed: {ref.name}")

    # ------------------------------------------------------------- project / recipe I/O
    def project(self, embed_unsaved: bool = True) -> Project:
        entries = []
        for it in self.items:
            embed = it.example_key is None and it.pattern.source_path is None and embed_unsaved
            entries.append(
                entry_for(
                    it.pattern,
                    it.recipe,
                    it.read_options,
                    it.example_key,
                    embed=embed,
                    background=it.background,
                )
            )
        return Project(entries, self.current_index(), self.plot_settings.to_dict())

    def save_project_to(self, path: str) -> None:
        save_project(self.project(), path)
        self.log(
            f"Project saved: {path} (raw files referenced by path + SHA-256; measured "
            "backgrounds embedded)"
        )

    def open_project_from(self, path: str) -> list[str]:
        try:
            proj = load_project(path)
        except ProjectError as exc:
            self.error("project", str(exc))
            return [str(exc)]
        problems = []
        for e in proj.entries:
            try:
                pat, recipe = resolve_entry(e)
                bg = resolve_background(e)
            except (ProjectError, XRDFormatError, OSError) as exc:
                problems.append(str(exc))
                self.log(f"  project entry skipped: {exc}")
                continue
            opts = ReadOptions.from_dict(e.read_options) if e.read_options else None
            self.add_item(DatasetItem(pat, recipe, opts, e.example_key, background=bg))
        self.plot_settings = PlotSettings.from_dict(proj.plot_settings)
        if proj.active is not None and 0 <= proj.active < len(self.items):
            self.data_list.setCurrentRow(proj.active)
        self.log(
            f"Project opened: {path} ({len(proj.entries) - len(problems)}/"
            f"{len(proj.entries)} datasets restored)"
        )
        if problems:
            self.error("project", "\n".join(problems))
        return problems

    def save_project_dialog(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, self.tr_("act.save_project"), "", "XRD Analyzer project (*.xrdproj.json)"
        )
        if path:
            try:
                self.save_project_to(path)
            except (ProjectError, OSError) as exc:
                self.error("project", str(exc))

    def open_project_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, self.tr_("act.open_project"), "", "XRD Analyzer project (*.xrdproj.json *.json)"
        )
        if path:
            self.open_project_from(path)

    def save_recipe_dialog(self) -> None:
        item = self.current()
        if item is None:
            return self.error("recipe", self.tr_("msg.no_data"))
        path, _ = QFileDialog.getSaveFileName(
            self, self.tr_("act.save_recipe"), "", "Recipe (*.json)"
        )
        if path:
            self.params.recipe(item.recipe).save(path)
            self.log(f"Recipe saved: {path}")
        return None

    def load_recipe_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, self.tr_("act.load_recipe"), "", "Recipe (*.json)"
        )
        if not path:
            return
        try:
            r = AnalysisRecipe.load(path)
        except (ValueError, OSError, TypeError) as exc:
            self.error("recipe", str(exc))
            return
        self.push_recipe(r, f"load recipe {Path(path).name}")

    # ------------------------------------------------------------------------- export
    def export_to(self, folder: str) -> list[Path]:
        item = self.current()
        if item is None or item.result is None:
            raise ValueError("nothing analysed yet")
        paths = export_all(item.result, folder, self.plot_settings)
        self.log(f"Exported {len(paths)} file(s) to {folder}")
        return paths

    def export_dialog(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, self.tr_("act.export"))
        if folder:
            try:
                self.export_to(folder)
            except (ValueError, OSError) as exc:
                self.error("export", str(exc))

    def export_figure_dialog(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, self.tr_("act.export_fig"), "", "PNG (*.png);;SVG (*.svg);;PDF (*.pdf)"
        )
        if path:
            try:
                save_figure(self.pattern_plot.figure, path)
                self.log(f"Figure saved: {path}")
            except (ValueError, OSError) as exc:
                self.error("figure", str(exc))

    def export_batch_dialog(self) -> None:
        if not self.batch_stats:
            return self.error("batch", "run a batch analysis first")
        path, _ = QFileDialog.getSaveFileName(self, self.tr_("act.export_batch"), "", "CSV (*.csv)")
        if path:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=list(self.batch_stats[0]))
                w.writeheader()
                for row in self.batch_stats:
                    w.writerow({**row, "patterns": ";".join(row["patterns"])})
            self.log(f"Batch statistics saved: {path}")
        return None

    # --------------------------------------------------------------------------- view
    def toggle_dark(self, on: bool) -> None:
        self.dark = bool(on)
        apply_theme(QApplication.instance(), self.dark)
        self.refresh_views()

    def set_language(self, code: str) -> None:
        self.tr_.lang = code
        self.retranslate()
        if getattr(self, "_selfupdate", None):
            self._selfupdate["retranslate"]()
        self._fill_summary(self.current())

    def _toggle_normalise(self, on: bool) -> None:
        self.normalise_overlay = bool(on)
        self.refresh_overlay()

    def change_font(self, delta: int) -> None:
        app = QApplication.instance()
        font = app.font()
        font.setPointSize(max(7, min(24, font.pointSize() + delta)))
        app.setFont(font)
        self.plot_settings.font_size = max(6.0, min(24.0, self.plot_settings.font_size + delta))
        self.refresh_views()

    def plot_settings_dialog(self) -> None:
        dlg = PlotSettingsDialog(self._ps(), self)
        if dlg.exec() == PlotSettingsDialog.DialogCode.Accepted:
            self.plot_settings = dlg.settings()
            self.refresh_views()

    def about(self) -> None:
        QMessageBox.about(
            self,
            APP_NAME,
            f"<b>{APP_NAME} {__version__}</b><br>{DISCLAIMER_EN}<br>{DISCLAIMER_KO}<br><br>"
            "Input: delimited text (2θ, intensity). Vendor binary formats are not supported.<br>"
            "License: MIT.",
        )

    def closeEvent(self, e: Any) -> None:
        # Never block the event loop or destroy a running thread: cancel, ignore this close,
        # and close again from _on_thread_finished once the worker has really stopped.
        if self.worker is not None:
            self._close_requested = True
            self._pending_run = False
            self.worker.request_cancel()
            self.log("Closing after the running analysis stops…")
            e.ignore()
            return
        super().closeEvent(e)
