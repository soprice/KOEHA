"""Windowed Windows entry point with a readable crash log instead of a console."""
import traceback

from koeha.__main__ import main

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    try:
        raise SystemExit(main())
    except Exception:
        from PySide6.QtWidgets import QApplication, QMessageBox

        from koeha.core.paths import data_root
        log = data_root() / "desktop-crash.log"
        log.write_text(traceback.format_exc(), encoding="utf-8")
        app = QApplication.instance() or QApplication([])
        QMessageBox.critical(None, "KOEHA", f"KOEHA could not start. Details: {log}")
        raise
