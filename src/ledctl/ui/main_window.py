"""Main window: sidebar + pages."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..protocol import DeviceState, model_by_name
from ..session import Session
from . import theme
from .pages import ColorPage, DevicePage, EffectsPage, MusicPage, SettingsPage
from .widgets import SliderRow, card, glyph_icon, icon_label, label

NAV = [
    ("bluetooth", "Devices"),
    ("color", "Color"),
    ("effects", "Effects"),
    ("music", "Music"),
    ("settings", "Settings"),
]

LINK_COLORS = {"connected": theme.SUCCESS, "connecting": theme.WARNING, "reconnecting": theme.WARNING,
               "disconnecting": theme.WARNING}
LINK_TEXTS = {"connected": "Connected", "connecting": "Connecting…", "reconnecting": "Reconnecting…",
              "disconnecting": "Disconnecting…", "disconnected": "Not connected"}


class PowerButton(QPushButton):
    """Large, explicit on/off button for the LEDs."""

    def __init__(self):
        super().__init__()
        self.setObjectName("power")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(44)
        self.setIconSize(QSize(18, 18))
        self.toggled.connect(self._refresh)
        self._refresh(False)

    def _refresh(self, on: bool) -> None:
        self.setIcon(glyph_icon("power", "#ffffff" if on else theme.MUTED, 18))
        self.setText("  LEDs on" if on else "  LEDs off")
        self.setToolTip("Click to turn the LEDs off" if on else "Click to turn the LEDs on")

    def set_on(self, on: bool) -> None:
        self.blockSignals(True)
        self.setChecked(on)
        self.blockSignals(False)
        self._refresh(on)


class MainWindow(QMainWindow):
    def __init__(self, session: Session):
        super().__init__()
        self.session = session
        self.setWindowTitle("LED Controller")
        self.resize(1180, 800)
        self.setMinimumSize(900, 620)

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        h.addWidget(self._build_sidebar())
        self.stack = QStackedWidget()
        h.addWidget(self.stack, 1)

        self.devices = DevicePage(session)
        self.pages = [self.devices, ColorPage(session), EffectsPage(session), MusicPage(session), SettingsPage(session)]
        for page in self.pages:
            self.stack.addWidget(page)
            page.go_devices.connect(lambda: self._go(0))
            page.set_protocol(None)
        self.nav_group.idClicked.connect(self._go)

        ble = session.ble
        ble.min_interval = session.config.write_interval_ms / 1000
        ble.auto_reconnect = session.config.auto_reconnect
        ble.scan_result.connect(self.devices.add_device)
        ble.scan_running.connect(self.devices.set_scanning)
        ble.link_changed.connect(self._on_link)
        ble.error.connect(self._on_error)
        session.engine.error.connect(self._on_error)
        session.protocol_changed.connect(self._on_protocol)
        session.state_changed.connect(self._on_state)

        self.devices.scan_requested.connect(self._scan)
        self.devices.connect_requested.connect(lambda addr, model: ble.connect_device(addr, model))
        self.devices.disconnect_requested.connect(ble.disconnect_device)

        self._go(0)
        cfg = session.config
        if cfg.auto_connect and cfg.last_address:
            QTimer.singleShot(300, lambda: ble.connect_device(cfg.last_address, model_by_name(cfg.last_model)))
        else:
            QTimer.singleShot(300, self._scan)

    # ------------------------------------------------------------------ sidebar
    def _build_sidebar(self) -> QWidget:
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(248)
        v = QVBoxLayout(side)
        v.setContentsMargins(16, 22, 16, 18)
        v.setSpacing(4)

        brand = QHBoxLayout()
        brand.addWidget(icon_label("brightness", 22, theme.ACCENT))
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(label("LED Controller", "appTitle"))
        col.addWidget(label("BanlanX · Bluetooth", "appSubtitle"))
        brand.addLayout(col, 1)
        v.addLayout(brand)
        v.addSpacing(22)

        self.nav_group = QButtonGroup(self)
        for i, (glyph, text) in enumerate(NAV):
            b = QPushButton(f"   {text}")
            b.setObjectName("nav")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setIcon(glyph_icon(glyph, theme.MUTED, 16, color_on=theme.ACCENT))
            b.setIconSize(QSize(16, 16))
            self.nav_group.addButton(b, i)
            v.addWidget(b)
        v.addStretch()

        # Quick controls: connection status, power and brightness.
        box = QVBoxLayout()
        box.setSpacing(12)
        status = QHBoxLayout()
        status.setSpacing(8)
        self.dot = label("●")
        self.dot.setStyleSheet(f"color: {theme.MUTED};")
        status.addWidget(self.dot)
        self.side_status = label("Not connected", "small")
        status.addWidget(self.side_status, 1)
        box.addLayout(status)
        self.power = PowerButton()
        box.addWidget(self.power)
        self.brightness = SliderRow("Brightness", 1, 255, self.session.config.brightness,
                                    lambda v: f"{round(v / 2.55)} %")
        box.addWidget(self.brightness)
        self.quick = card(box)
        self.quick.layout().setContentsMargins(14, 12, 14, 14)
        v.addWidget(self.quick)

        self.power.toggled.connect(self.session.power)
        self.brightness.changed.connect(self.session.set_brightness)
        self._set_quick_enabled(False)
        return side

    def _set_quick_enabled(self, on: bool) -> None:
        self.power.setEnabled(on)
        self.brightness.setEnabled(on)

    # ------------------------------------------------------------------ events
    def _go(self, index: int) -> None:
        self.nav_group.button(index).setChecked(True)
        self.stack.setCurrentIndex(index)

    def _scan(self) -> None:
        self.devices.clear_devices()
        self.session.ble.scan()

    def _on_link(self, link: str, message: str) -> None:
        self.devices.on_link(link, message)
        self.dot.setStyleSheet(f"color: {LINK_COLORS.get(link, theme.MUTED)};")
        p = self.session.protocol
        text = LINK_TEXTS.get(link, link)
        if link == "connected" and p:
            text += f" · {p.model.name}"
        self.side_status.setText(text)

    def _on_protocol(self, protocol) -> None:
        for page in self.pages:
            page.set_protocol(protocol)
        self._set_quick_enabled(protocol is not None)
        self._route_pending = protocol is not None  # open the right tab once the first state arrives
        if protocol is not None:
            self.devices.on_link("connected", "")
            self.side_status.setText(f"Connected · {protocol.model.name}")

    def _on_state(self, st: DeviceState) -> None:
        if st.power is not None:
            self.power.set_on(st.power)
        if st.brightness is not None and not self.brightness.slider.isSliderDown():
            self.brightness.set_value(st.brightness)
        for page in self.pages:
            page.apply_state(st)
        if getattr(self, "_route_pending", False) and self.stack.currentIndex() == 0:
            self._route_pending = False
            self._open_tab_for(st)

    def _open_tab_for(self, st: DeviceState) -> None:
        """Shows the tab matching what the controller is currently playing."""
        p = self.session.protocol
        eff = p.effect(st.effect) if p else None
        music = self.pages[3]
        if st.light_mode == 2 or (eff is not None and eff.kind == "sound"):
            music.show_source(music.PC if st.audio_input == 1 and p.supports_audio_feed else music.MIC)
            self._go(3)
        elif st.light_mode == 1 or (eff is not None and eff.kind == "dynamic"):
            self._go(2)
        else:
            self._go(1)

    def _on_error(self, message: str) -> None:
        QMessageBox.warning(self, "LED Controller", message)

    def closeEvent(self, event) -> None:
        self.session.engine.stop()
        self.session.config.save()
        self.session.ble.shutdown()
        super().closeEvent(event)
