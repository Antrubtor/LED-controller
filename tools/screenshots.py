"""Generates the README screenshots with a simulated controller.

Nothing personal ends up in the images: the controller is fake (address AA:BB:CC:DD:EE:FF), the saved
settings are not loaded, no Bluetooth scan is made and the PC's audio devices are not listed.

    uv run tools/screenshots.py            # writes docs/screenshots/*.png
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ledctl.audio import capture  # noqa: E402
from ledctl.audio.engine import MusicEngine  # noqa: E402
from ledctl.ble.controller import BleController, Discovered  # noqa: E402
from ledctl.config import Config  # noqa: E402
from ledctl.protocol import DeviceState, model_by_name  # noqa: E402
from ledctl.session import Session  # noqa: E402
from ledctl.ui import theme  # noqa: E402
from ledctl.ui.main_window import MainWindow  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "docs" / "screenshots"
FAKE_ADDRESS = "AA:BB:CC:DD:EE:FF"

capture.list_devices = lambda: []  # do not show the PC's real audio devices


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    app.setStyle("Fusion")
    app.setFont(theme.ui_font())
    app.setStyleSheet(theme.STYLESHEET)

    config = Config()  # defaults, not the saved settings
    config.save = lambda: None
    config.auto_connect = False
    ble = BleController()
    ble.scan = lambda *a, **k: None
    session = Session(ble, MusicEngine(ble.send), config)
    window = MainWindow(session)
    window.resize(1180, 820)
    window.show()
    music = window.pages[3]

    def shoot(name: str) -> None:
        app.processEvents()
        window.grab().save(str(OUT / f"{name}.png"))
        print("saved", OUT / f"{name}.png")

    def devices() -> None:
        sp611e = model_by_name("SP611E")
        window.devices.add_device(Discovered(FAKE_ADDRESS, "SP611E", -58, sp611e, True))
        shoot("devices")
        protocol = sp611e.protocol(sp611e)
        session._on_connected(protocol, FAKE_ADDRESS)
        ble.link_changed.emit("connected", FAKE_ADDRESS)
        session._on_state(DeviceState(power=True, light_mode=0, effect=0x0E, brightness=220, speed=6, length=60,
                                      rgb=(124, 92, 255), sensitivity=9, audio_input=0, chip_order=2))
        QTimer.singleShot(600, pages)

    def pages() -> None:
        window._go(1)
        shoot("color")
        window._go(2)
        shoot("effects")
        window._go(3)
        music.show_source(music.PC)
        music.fx_grid.select(0xC9)
        music._show_effect_settings(0xC9)
        bands = np.clip(np.array([0.9, 0.95, 0.8, 0.7, 0.75, 0.6, 0.55, 0.65, 0.5, 0.45, 0.5, 0.4,
                                  0.35, 0.42, 0.3, 0.28, 0.33, 0.25, 0.2, 0.22, 0.15, 0.12, 0.1, 0.08]), 0, 1)
        music.spectrum.push(bands, (124, 92, 255), 0.9, False)
        shoot("music")
        music.show_source(music.STRIP)
        music.strip_spectrum.push(bands * 0.9, (255, 60, 40), 0.85, False)
        shoot("strip-modes")
        window._go(4)
        shoot("settings")
        app.quit()

    QTimer.singleShot(400, devices)
    app.exec()
    ble.shutdown()


if __name__ == "__main__":
    main()
