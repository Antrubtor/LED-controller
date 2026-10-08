"""Application entry point."""

from __future__ import annotations

import logging
import signal
import sys


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("bleak").setLevel(logging.WARNING)

    from PySide6.QtWidgets import QApplication

    from .audio.engine import MusicEngine
    from .ble.controller import BleController
    from .config import Config
    from .session import Session
    from .ui import theme
    from .ui.main_window import MainWindow

    signal.signal(signal.SIGINT, signal.SIG_DFL)
    app = QApplication(sys.argv)
    app.setApplicationName("LED Controller")
    app.setStyle("Fusion")
    app.setFont(theme.ui_font())
    app.setStyleSheet(theme.STYLESHEET)

    config = Config.load()
    ble = BleController()
    engine = MusicEngine(ble.send)
    session = Session(ble, engine, config)
    window = MainWindow(session)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
