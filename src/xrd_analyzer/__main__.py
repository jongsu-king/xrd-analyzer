"""``python -m xrd_analyzer`` starts the GUI; with arguments it runs the CLI.

Absolute imports so the same file also works as a PyInstaller entry script.
"""

import sys

if __name__ == "__main__":
    if len(sys.argv) > 1:
        from xrd_analyzer.cli import main

        raise SystemExit(main())
    from xrd_analyzer.ui.app import main as gui_main

    raise SystemExit(gui_main())
