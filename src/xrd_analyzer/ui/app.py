"""GUI entry point. Uncaught exceptions are logged in the window instead of killing the app."""

from __future__ import annotations

import sys
import traceback
from types import TracebackType

from PySide6.QtCore import QLocale
from PySide6.QtWidgets import QApplication

from .. import APP_NAME
from .main_window import MainWindow
from .theme import apply_theme


def main(argv: list[str] | None = None) -> int:
    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    apply_theme(app, dark=False)
    lang = "ko" if QLocale.system().language() == QLocale.Language.Korean else "en"
    win = MainWindow(lang)

    def hook(t: type[BaseException], v: BaseException, tb: TracebackType | None) -> None:
        win.log("UNCAUGHT: " + "".join(traceback.format_exception(t, v, tb)))

    sys.excepthook = hook
    win.show()
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
