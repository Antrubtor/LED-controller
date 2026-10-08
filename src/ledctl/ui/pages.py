"""Application pages."""

from __future__ import annotations

import math
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QColorDialog,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..audio import capture
from ..audio.visualizers import VISUALIZERS
from ..ble.controller import Discovered
from ..protocol import MODELS, DeviceState, Effect, Protocol, chip_orders
from ..session import Session
from . import theme
from .widgets import (
    ColorPreview,
    ColorWheel,
    EffectCard,
    FlowLayout,
    Segmented,
    SliderRow,
    SpectrumView,
    Swatch,
    Toggle,
    card,
    icon_button,
    icon_label,
    label,
    set_icon_button,
)

PRESETS = [
    "#ff0000", "#ff4d00", "#ff9900", "#ffd000", "#b6ff00", "#00ff3c", "#00ffaa", "#00e5ff",
    "#0077ff", "#1f2bff", "#7c3bff", "#c400ff", "#ff00c8", "#ff2a6d", "#ffffff", "#ffd6a0",
]


# The strip modes use the same 1–16 sensitivity scale as the controller. Each step multiplies the analyzer
# gain by the same factor: level 1 = ×0.3, level 16 = ×3 (the analyzer default ×1 is level 9).
def sensitivity_gain(level: int) -> float:
    return 0.3 * 10 ** ((level - 1) / 15)


def sensitivity_level(gain: float) -> int:
    return max(1, min(16, round(1 + 15 * math.log10(max(gain, 0.3) / 0.3))))


def _toggle_row(text: str, hint: str = "") -> tuple[QHBoxLayout, Toggle]:
    t = Toggle()
    col = QVBoxLayout()
    col.setSpacing(2)
    col.addWidget(label(text))
    if hint:
        col.addWidget(label(hint, "small", wrap=True))
    lay = QHBoxLayout()
    lay.addLayout(col, 1)
    lay.addWidget(t, 0, Qt.AlignVCenter)
    return lay, t


def _select_card(group: QButtonGroup, key) -> None:
    group.setExclusive(False)
    for b in group.buttons():
        b.setChecked(b.key == key)
    group.setExclusive(True)


class Page(QWidget):
    go_devices = Signal()

    def __init__(self, title: str, subtitle: str, needs_device: bool = True):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        inner = QWidget()
        scroll.setWidget(inner)
        self.layout_ = QVBoxLayout(inner)
        self.layout_.setContentsMargins(36, 30, 36, 30)
        self.layout_.setSpacing(18)
        self.layout_.addWidget(label(title, "pageTitle"))
        self.layout_.addWidget(label(subtitle, "pageSubtitle", wrap=True))

        self.needs_device = needs_device
        self.banner = None
        if needs_device:
            lay = QHBoxLayout()
            lay.addWidget(icon_label("bluetooth", 18, theme.ACCENT))
            lay.addWidget(label("No controller connected. Connect one to control your LEDs."), 1)
            btn = QPushButton("Connect")
            btn.setObjectName("primary")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(self.go_devices)
            lay.addWidget(btn)
            self.banner = card(lay, "banner")
            self.layout_.addWidget(self.banner)

        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(18)
        self.layout_.addWidget(self.body)
        self.layout_.addStretch()

    def set_protocol(self, protocol: Protocol | None) -> None:
        if self.needs_device:
            self.banner.setVisible(protocol is None)
            self.body.setEnabled(protocol is not None)

    def apply_state(self, st: DeviceState) -> None:
        pass


# ============================================================================ Devices
class DeviceRow(QFrame):
    connect_clicked = Signal(object)

    def __init__(self, dev: Discovered):
        super().__init__()
        self.setObjectName("card")
        self.dev = dev
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 12, 12, 12)
        lay.setSpacing(14)
        self.icon = icon_label("bluetooth", 20, theme.ACCENT if dev.compatible else theme.MUTED)
        lay.addWidget(self.icon)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.name = label(dev.name)
        self.name.setStyleSheet("font-weight: 700; font-size: 10.5pt;")
        self.sub = label("", "small")
        col.addWidget(self.name)
        col.addWidget(self.sub)
        lay.addLayout(col, 1)
        self.signal = label("", "muted")
        lay.addWidget(self.signal)
        self.btn = QPushButton("Connect")
        self.btn.setObjectName("primary" if dev.compatible else "")
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.clicked.connect(lambda: self.connect_clicked.emit(self.dev))
        lay.addWidget(self.btn)
        self.update_dev(dev)

    def update_dev(self, dev: Discovered) -> None:
        self.dev = dev
        self.name.setText(dev.name)
        model = dev.model.name if dev.model else ("Probably BanlanX compatible" if dev.compatible else "Not recognized")
        self.sub.setText(f"{model}  ·  {dev.address}")
        bars = 4 if dev.rssi > -60 else 3 if dev.rssi > -70 else 2 if dev.rssi > -80 else 1
        self.signal.setText("▮" * bars + "▯" * (4 - bars) + f"  {dev.rssi} dBm")


class DevicePage(Page):
    connect_requested = Signal(str, object)  # address, Model | None
    disconnect_requested = Signal()
    scan_requested = Signal()

    def __init__(self, session: Session):
        super().__init__("Devices", "Find your Bluetooth controller and connect to it. "
                                    "The model is detected automatically.", needs_device=False)
        self.session = session
        self.rows: dict[str, DeviceRow] = {}
        self._link = "disconnected"

        # --- Status
        lay = QHBoxLayout()
        lay.setSpacing(16)
        self.status_icon = icon_label("bluetooth", 28, theme.MUTED)
        lay.addWidget(self.status_icon)
        col = QVBoxLayout()
        col.setSpacing(3)
        self.status_title = label("Not connected", "sectionTitle")
        self.status_title.setStyleSheet("font-size: 13pt; font-weight: 700;")
        self.status_detail = label("No controller connected", "muted", wrap=True)
        col.addWidget(self.status_title)
        col.addWidget(self.status_detail)
        lay.addLayout(col, 1)
        self.btn_reconnect = icon_button("link", "Reconnect", "primary")
        self.btn_reconnect.clicked.connect(self._reconnect_last)
        self.btn_disconnect = icon_button("unlink", "Disconnect", "danger")
        self.btn_disconnect.clicked.connect(self.disconnect_requested)
        lay.addWidget(self.btn_reconnect)
        lay.addWidget(self.btn_disconnect)
        self.body_layout.addWidget(card(lay))

        # --- Scan
        box = QVBoxLayout()
        head = QHBoxLayout()
        head.addWidget(label("Nearby devices", "sectionTitle"))
        head.addStretch()
        self.show_all = Toggle()
        head.addWidget(label("Show all", "muted"))
        head.addWidget(self.show_all)
        head.addSpacing(10)
        self.btn_scan = icon_button("refresh", "Scan", "primary")
        self.btn_scan.clicked.connect(self.scan_requested)
        head.addWidget(self.btn_scan)
        box.addLayout(head)
        self.empty = label("Start a scan to find your controller (it must be powered and not connected "
                           "to the mobile app).", "muted", wrap=True)
        box.addWidget(self.empty)
        self.list_layout = QVBoxLayout()
        self.list_layout.setSpacing(8)
        box.addLayout(self.list_layout)
        self.show_all.toggled.connect(self._refilter)
        self.body_layout.addWidget(card(box))

        # --- Options
        opts = QVBoxLayout()
        opts.addWidget(label("Connection options", "sectionTitle"))
        row, self.auto_connect = _toggle_row("Connect on startup",
                                             "Reconnects to the last used controller when the app starts.")
        opts.addLayout(row)
        row, self.auto_reconnect = _toggle_row("Automatic reconnection",
                                               "If the connection drops, the app retries several times.")
        opts.addLayout(row)
        model_row = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(label("Controller model"))
        col.addWidget(label("Keep \"Automatic detection\" unless your model is detected wrongly.", "small", wrap=True))
        model_row.addLayout(col, 1)
        self.model_combo = QComboBox()
        self.model_combo.addItem("Automatic detection", None)
        for m in MODELS:
            self.model_combo.addItem(f"{m.name} — {m.description}", m)
        self.model_combo.setMinimumWidth(280)
        model_row.addWidget(self.model_combo)
        opts.addLayout(model_row)
        self.body_layout.addWidget(card(opts))

        cfg = session.config
        self.auto_connect.setChecked(cfg.auto_connect)
        self.auto_reconnect.setChecked(cfg.auto_reconnect)
        self.auto_connect.toggled.connect(lambda v: (setattr(cfg, "auto_connect", v), cfg.save()))
        self.auto_reconnect.toggled.connect(self._set_auto_reconnect)
        self._set_link("disconnected", "")

    def _set_auto_reconnect(self, v: bool) -> None:
        self.session.config.auto_reconnect = v
        self.session.ble.auto_reconnect = v
        self.session.config.save()

    def _reconnect_last(self) -> None:
        if self.session.config.last_address:
            self.connect_requested.emit(self.session.config.last_address, self.model_combo.currentData())

    def add_device(self, dev: Discovered) -> None:
        if dev.address in self.rows:
            self.rows[dev.address].update_dev(dev)
            return
        row = DeviceRow(dev)
        row.connect_clicked.connect(lambda d: self.connect_requested.emit(d.address, self.model_combo.currentData() or d.model))
        self.rows[dev.address] = row
        # Recognized controllers first.
        index = self.list_layout.count() if not dev.compatible else sum(1 for r in self.rows.values() if r.dev.compatible) - 1
        self.list_layout.insertWidget(index, row)
        self._refilter()

    def clear_devices(self) -> None:
        for row in self.rows.values():
            row.deleteLater()
        self.rows.clear()
        self._refilter()

    def _refilter(self) -> None:
        show_all = self.show_all.isChecked()
        visible = 0
        for row in self.rows.values():
            shown = show_all or row.dev.compatible
            row.setVisible(shown)
            visible += shown
        self.empty.setVisible(visible == 0)
        hidden = sum(1 for r in self.rows.values() if not r.dev.compatible)
        if visible == 0 and hidden:
            self.empty.setText(f"No compatible controller found ({hidden} other Bluetooth device(s)). "
                               "Turn on \"Show all\" to see them.")

    def set_scanning(self, running: bool) -> None:
        self.btn_scan.setEnabled(not running)
        set_icon_button(self.btn_scan, text="Scanning…" if running else "Scan")
        if running:
            self.empty.setText("Scanning…")

    def _set_link(self, link: str, message: str) -> None:
        self._link = link
        cfg = self.session.config
        p = self.session.protocol
        colors = {"connected": theme.SUCCESS, "connecting": theme.WARNING, "reconnecting": theme.WARNING,
                  "disconnecting": theme.WARNING}
        self.status_icon.setStyleSheet(f"color: {colors.get(link, theme.MUTED)};")
        if link == "connected" and p is not None:
            self.status_title.setText(f"Connected to {p.model.name}")
            self.status_detail.setText(f"{p.model.description}  ·  {p.family}  ·  {cfg.last_address}")
        elif link == "connecting":
            self.status_title.setText("Connecting…")
            self.status_detail.setText(message)
        elif link == "reconnecting":
            self.status_title.setText("Reconnecting…")
            self.status_detail.setText(message)
        elif link == "disconnecting":
            self.status_title.setText("Disconnecting…")
            self.status_detail.setText("")
        else:
            self.status_title.setText("Not connected")
            self.status_detail.setText(
                (message + "  ·  " if message else "") +
                (f"Last device: {cfg.last_address}" if cfg.last_address else "No controller connected"))
        busy = link in ("connecting", "reconnecting", "disconnecting")
        self.btn_disconnect.setVisible(link in ("connected", "connecting", "reconnecting"))
        self.btn_reconnect.setVisible(link == "disconnected" and bool(cfg.last_address))
        for row in self.rows.values():
            row.btn.setEnabled(not busy)

    def on_link(self, link: str, message: str) -> None:
        self._set_link(link, message)


# ============================================================================ Color
class ColorPage(Page):
    def __init__(self, session: Session):
        super().__init__("Color", "Pick a solid color for the whole strip.")
        self.session = session
        grid = QHBoxLayout()
        grid.setSpacing(18)

        left = QVBoxLayout()
        self.wheel = ColorWheel()
        self.wheel.setMinimumSize(300, 300)
        left.addWidget(self.wheel, 1)
        wheel_card = card(left)
        grid.addWidget(wheel_card, 3)

        right = QVBoxLayout()
        top = QHBoxLayout()
        self.preview = ColorPreview(110)
        top.addWidget(self.preview)
        col = QVBoxLayout()
        col.addWidget(label("Color code", "muted"))
        self.hex = QLineEdit()
        self.hex.setMaxLength(7)
        self.hex.setPlaceholderText("#RRGGBB")
        self.hex.setFixedWidth(130)
        col.addWidget(self.hex)
        col.addStretch()
        top.addLayout(col)
        top.addStretch()
        right.addLayout(top)
        right.addWidget(label("Presets", "sectionTitle"))
        presets = QWidget()
        flow = FlowLayout(presets, spacing=8)
        for hx in PRESETS:
            sw = Swatch(QColor(hx))
            sw.clicked.connect(lambda _=False, c=hx: self._pick(QColor(c), commit=True))
            flow.addWidget(sw)
        right.addWidget(presets)

        right.addWidget(label("My colors", "sectionTitle"))
        self.custom = QWidget()
        self.custom_flow = FlowLayout(self.custom, spacing=8)
        right.addWidget(self.custom)
        add = QPushButton("＋  Save current color")
        add.setCursor(Qt.PointingHandCursor)
        add.clicked.connect(self._save_custom)
        right.addWidget(add, 0, Qt.AlignLeft)

        self.white_box = QWidget()
        wl = QVBoxLayout(self.white_box)
        wl.setContentsMargins(0, 8, 0, 0)
        self.white = SliderRow("White (W LEDs)", 0, 255, 255, lambda v: f"{round(v / 2.55)} %")
        wl.addWidget(self.white)
        right.addWidget(self.white_box)
        right.addStretch()
        grid.addWidget(card(right), 2)
        self.body_layout.addLayout(grid)

        self.wheel.colorChanged.connect(lambda c: self._pick(c, commit=False))
        self.hex.editingFinished.connect(self._from_hex)
        self.white.changed.connect(session.set_white)
        self._render_custom()
        self._show(QColor(*session.config.color))

    def set_protocol(self, protocol):
        super().set_protocol(protocol)
        self.white_box.setVisible(protocol is not None and protocol.effect_white is not None)

    def _show(self, c: QColor) -> None:
        self.wheel.set_color(c)
        self.preview.set_color(c)
        if not self.hex.hasFocus():
            self.hex.setText(c.name().upper())

    def _pick(self, c: QColor, commit: bool) -> None:
        self.preview.set_color(c)
        self.hex.setText(c.name().upper())
        if commit:
            self.wheel.set_color(c)
        self.session.set_color((c.red(), c.green(), c.blue()))

    def _from_hex(self) -> None:
        c = QColor(self.hex.text().strip())
        if c.isValid():
            self._pick(c, commit=True)

    def _save_custom(self) -> None:
        hx = self.wheel.color().name()
        colors = self.session.config.custom_colors
        if hx not in colors:
            colors.insert(0, hx)
            del colors[16:]
            self.session.config.save()
            self._render_custom()

    def _render_custom(self) -> None:
        while self.custom_flow.count():
            item = self.custom_flow.takeAt(0)
            item.widget().deleteLater()
        for hx in self.session.config.custom_colors:
            sw = Swatch(QColor(hx))
            sw.clicked.connect(lambda _=False, c=hx: self._pick(QColor(c), commit=True))
            self.custom_flow.addWidget(sw)
        self.custom.setVisible(bool(self.session.config.custom_colors))

    def apply_state(self, st: DeviceState) -> None:
        p = self.session.protocol
        if st.rgb and p and st.effect == p.effect_solid and not self.wheel.underMouse():
            self._show(QColor(*st.rgb))
        if st.white is not None:
            self.white.set_value(st.white)


# ============================================================================ Effects
class EffectGrid(QWidget):
    """Filterable grid of effect cards."""

    selected = Signal(int)

    def __init__(self, card_width: int = 168):
        super().__init__()
        self.card_width = card_width
        self.flow = FlowLayout(self, spacing=10, fill_min_width=card_width)
        self.group = QButtonGroup(self)
        self.cards: list[EffectCard] = []

    def set_effects(self, effects: list[Effect]) -> None:
        for c in self.cards:
            self.group.removeButton(c)
            c.deleteLater()
        while self.flow.count():
            self.flow.takeAt(0)
        self.cards = []
        for e in effects:
            c = EffectCard(e.id, e.name, e.colors, width=self.card_width)
            c.category = e.category
            c.clicked.connect(lambda _=False, i=e.id: self.selected.emit(i))
            self.group.addButton(c)
            self.flow.addWidget(c)
            self.cards.append(c)
        self.flow.invalidate()

    def filter(self, text: str, category: str | None) -> int:
        text = text.strip().lower()
        n = 0
        for c in self.cards:
            ok = (not text or text in c.title.lower()) and (category is None or c.category == category)
            c.setVisible(ok)
            n += ok
        self.flow.invalidate()
        self.updateGeometry()
        return n

    def select(self, effect_id) -> None:
        _select_card(self.group, effect_id)


class EffectsPage(Page):
    def __init__(self, session: Session):
        super().__init__("Effects", "Animations built into the controller. They keep running on their own, "
                                   "even with the PC turned off.")
        self.session = session

        ctl = QGridLayout()
        ctl.setHorizontalSpacing(28)
        ctl.setVerticalSpacing(14)
        self.speed = SliderRow("Speed", 1, 10, 5)
        self.length = SliderRow("Effect length", 1, 150, 50)
        ctl.addWidget(self.speed, 0, 0)
        ctl.addWidget(self.length, 0, 1)
        self.body_layout.addWidget(card(ctl))

        filt = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search effects…  (e.g. comet, blue, rainbow)")
        self.search.setClearButtonEnabled(True)
        filt.addWidget(self.search, 1)
        self.count = label("", "muted")
        filt.addWidget(self.count)
        self.body_layout.addLayout(filt)

        self.chips_box = QWidget()
        self.chips = FlowLayout(self.chips_box, spacing=8)
        self.chip_group = QButtonGroup(self)
        self.body_layout.addWidget(self.chips_box)

        self.grid = EffectGrid()
        self.body_layout.addWidget(self.grid)

        self.grid.selected.connect(session.set_effect)
        self.speed.changed.connect(session.set_speed)
        self.length.changed.connect(session.set_length)
        self.search.textChanged.connect(self._filter)
        self.chip_group.idClicked.connect(lambda _: self._filter())

    def set_protocol(self, protocol):
        super().set_protocol(protocol)
        if protocol is None:
            return
        effects = protocol.dynamic_effects()
        self.grid.set_effects(effects)
        self.speed.set_range(1, protocol.max_speed)
        self.length.setVisible(protocol.max_length is not None)
        if protocol.max_length:
            self.length.set_range(1, protocol.max_length)
        for b in self.chip_group.buttons():
            self.chip_group.removeButton(b)
            b.deleteLater()
        while self.chips.count():
            self.chips.takeAt(0)
        cats = list(dict.fromkeys(e.category for e in effects))
        for i, cat in enumerate(["All", *cats]):
            b = QPushButton(cat)
            b.setObjectName("chip")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.category = None if i == 0 else cat
            self.chip_group.addButton(b, i)
            self.chips.addWidget(b)
        self.chip_group.button(0).setChecked(True)
        self._filter()

    def _filter(self):
        btn = self.chip_group.checkedButton()
        n = self.grid.filter(self.search.text(), btn.category if btn else None)
        self.count.setText(f"{n} effect{'s' if n != 1 else ''}")

    def apply_state(self, st: DeviceState) -> None:
        if st.effect is not None:
            self.grid.select(st.effect if st.light_mode in (0, None) else None)
        if st.speed and not self.speed.slider.isSliderDown():
            self.speed.set_value(st.speed)
        if st.length and not self.length.slider.isSliderDown():
            self.length.set_value(st.length)


# ============================================================================ Music
class ColorChooser(QWidget):
    """Small row of swatches + an "Other…" button."""

    picked = Signal(tuple)

    def __init__(self, initial: tuple[int, int, int]):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.current = ColorPreview(34)
        self.current.setFixedSize(34, 34)
        lay.addWidget(self.current)
        lay.addSpacing(6)
        for hx in ("#ff0000", "#ff9900", "#ffd000", "#00ff3c", "#00e5ff", "#1f2bff", "#7c3bff", "#ff00c8", "#ffffff"):
            sw = Swatch(QColor(hx), 26)
            sw.clicked.connect(lambda _=False, c=hx: self._set(QColor(c)))
            lay.addWidget(sw)
        other = QPushButton("Other…")
        other.setCursor(Qt.PointingHandCursor)
        other.clicked.connect(self._dialog)
        lay.addWidget(other)
        lay.addStretch()
        self.color = QColor(*initial)
        self.current.set_color(self.color)

    def _dialog(self):
        c = QColorDialog.getColor(self.color, self, "Pick a color")
        if c.isValid():
            self._set(c)

    def _set(self, c: QColor):
        self.color = c
        self.current.set_color(c)
        self.picked.emit((c.red(), c.green(), c.blue()))

    def set_color(self, rgb):
        self.color = QColor(*rgb)
        self.current.set_color(self.color)


class MusicPage(Page):
    PC, MIC, STRIP = range(3)

    def __init__(self, session: Session):
        super().__init__("Music", "The controller's music effects, driven by your PC audio or by its own "
                                  "microphone — or whole-strip modes computed on the PC.")
        self.session = session
        cfg = session.config
        engine = session.engine

        self.source = Segmented(["  PC audio  ", "  Controller microphone  ", "  Strip modes  "])
        self.body_layout.addWidget(self.source, 0, Qt.AlignLeft)
        self.stack = QStackedWidget()
        self.body_layout.addWidget(self.stack)

        # ---------------------------------------------------------------- built-in music effects
        fx = QWidget()
        fxl = QVBoxLayout(fx)
        fxl.setContentsMargins(0, 0, 0, 0)
        fxl.setSpacing(18)
        top = QHBoxLayout()
        top.setSpacing(18)

        # Left card: what is playing (PC stream controls or microphone note).
        live = QVBoxLayout()
        head = QHBoxLayout()
        self.live_title = label("Stopped", "sectionTitle")
        head.addWidget(self.live_title)
        head.addStretch()
        self.fps_label = label("", "small")
        head.addWidget(self.fps_label)
        live.addLayout(head)
        self.source_note = label("", "muted", wrap=True)
        live.addWidget(self.source_note)
        self.pc_box = QWidget()
        pl = QVBoxLayout(self.pc_box)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(10)
        self.spectrum = SpectrumView()
        pl.addWidget(self.spectrum)
        self.btn_start = icon_button("play", "Start", "primary")
        self.btn_start.setMinimumHeight(44)
        pl.addWidget(self.btn_start)
        pl.addWidget(label("Audio source", None))
        pl.addWidget(label("PC sound (what your speakers play) or a microphone plugged into the PC — the "
                           "equivalent of the app's phone player and phone microphone.", "small", wrap=True))
        self.audio_combo = QComboBox()
        # Device names can be very long: do not let them dictate the page width.
        self.audio_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.audio_combo.setMinimumContentsLength(16)
        pl.addWidget(self.audio_combo)
        self.pc_fps = SliderRow("Frames per second", 10, 30, cfg.music_fps, lambda v: f"{v} fps")
        pl.addWidget(self.pc_fps)
        live.addWidget(self.pc_box)
        live.addStretch()
        top.addWidget(card(live), 3)

        # Right card: the settings of the selected effect, like in the BanlanX app.
        opts = QVBoxLayout()
        opts.addWidget(label("Settings", "sectionTitle"))
        self.fx_sens = SliderRow("Sensitivity", 1, 16, 8)
        opts.addWidget(self.fx_sens)
        self.fx_length = SliderRow("Effect length", 1, 150, 60)
        opts.addWidget(self.fx_length)
        self.fx_color_box = QWidget()
        cl = QVBoxLayout(self.fx_color_box)
        cl.setContentsMargins(0, 4, 0, 0)
        cl.addWidget(label("Color", None))
        self.fx_color = ColorChooser(cfg.color)
        cl.addWidget(self.fx_color)
        opts.addWidget(self.fx_color_box)
        opts.addStretch()
        top.addWidget(card(opts), 2)
        fxl.addLayout(top)

        self.fx_grid = EffectGrid(card_width=210)
        fxl.addWidget(self.fx_grid)
        self.stack.addWidget(fx)

        # ---------------------------------------------------------------- whole-strip modes
        strip = QWidget()
        sl = QVBoxLayout(strip)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(18)
        stop = QHBoxLayout()
        stop.setSpacing(18)
        slive = QVBoxLayout()
        shead = QHBoxLayout()
        self.strip_title = label("Stopped", "sectionTitle")
        shead.addWidget(self.strip_title)
        shead.addStretch()
        slive.addLayout(shead)
        slive.addWidget(label("The PC computes one color for the whole strip and streams it.", "muted", wrap=True))
        self.strip_spectrum = SpectrumView()
        slive.addWidget(self.strip_spectrum)
        self.btn_strip = icon_button("play", "Start", "primary")
        self.btn_strip.setMinimumHeight(44)
        slive.addWidget(self.btn_strip)
        stop.addWidget(card(slive), 3)

        sopts = QVBoxLayout()
        sopts.addWidget(label("Settings", "sectionTitle"))
        self.sens = SliderRow("Sensitivity", 1, 16, sensitivity_level(cfg.music_sensitivity))
        self.smooth = SliderRow("Smoothing", 3, 60, round(cfg.music_smoothing * 100), lambda v: f"{v * 10} ms")
        self.fps = SliderRow("Frames per second", 10, 30, cfg.music_fps, lambda v: f"{v} fps")
        self.floor = SliderRow("Minimum brightness", 0, 40, round(cfg.music_floor * 100), lambda v: f"{v} %")
        for w in (self.sens, self.smooth, self.fps, self.floor):
            sopts.addWidget(w)
        self.color_box = QWidget()
        scl = QVBoxLayout(self.color_box)
        scl.setContentsMargins(0, 4, 0, 0)
        scl.addWidget(label("Color", None))
        self.color = ColorChooser(cfg.music_color)
        scl.addWidget(self.color)
        sopts.addWidget(self.color_box)
        self.anim_box = QWidget()
        al = QVBoxLayout(self.anim_box)
        al.setContentsMargins(0, 4, 0, 0)
        al.addWidget(label("Animated effect", None))
        self.anim_combo = QComboBox()
        al.addWidget(self.anim_combo)
        sopts.addWidget(self.anim_box)
        sopts.addStretch()
        stop.addWidget(card(sopts), 2)
        sl.addLayout(stop)

        self.mode_desc = label("", "muted", wrap=True)
        sl.addWidget(self.mode_desc)
        modes = QWidget()
        mflow = FlowLayout(modes, spacing=10, fill_min_width=176)
        self.mode_group = QButtonGroup(self)
        for v in VISUALIZERS:
            if v.native_feed:
                continue  # shown as the "PC audio" tab
            c = EffectCard(v.id, v.name, v.preview, width=176)
            c.setToolTip(v.description)
            c.clicked.connect(lambda _=False, i=v.id: self._choose_mode(i))
            self.mode_group.addButton(c)
            mflow.addWidget(c)
        sl.addWidget(modes)
        self.stack.addWidget(strip)

        # ---------------------------------------------------------------- wiring
        self.source.changed.connect(self._on_source)
        self.fx_grid.selected.connect(self._play_effect)
        self.fx_sens.changed.connect(session.set_sensitivity)
        self.fx_length.changed.connect(session.set_length)
        self.fx_color.picked.connect(session.set_effect_color)
        self.btn_start.clicked.connect(self._toggle_pc_effects)
        self.btn_strip.clicked.connect(self._toggle_strip)
        self.sens.changed.connect(lambda v: self._set_engine("sensitivity", sensitivity_gain(v), "music_sensitivity"))
        self.smooth.changed.connect(lambda v: self._set_engine("smoothing", v / 100, "music_smoothing"))
        self.fps.changed.connect(lambda v: self._set_fps(v, self.pc_fps))
        self.pc_fps.changed.connect(lambda v: self._set_fps(v, self.fps))
        self.floor.changed.connect(self._set_floor)
        self.color.picked.connect(self._set_music_color)
        self.anim_combo.currentIndexChanged.connect(
            lambda _: self.anim_combo.currentData() is not None and session.set_animated_effect(self.anim_combo.currentData()))
        self.audio_combo.currentIndexChanged.connect(self._set_audio_device)
        engine.running_changed.connect(self._on_running)
        engine.frame.connect(self._on_frame)

        # initial engine state
        engine.sensitivity = cfg.music_sensitivity
        engine.smoothing = cfg.music_smoothing
        engine.fps = cfg.music_fps
        engine.params.floor = cfg.music_floor
        engine.params.color = tuple(cfg.music_color)
        strip_mode = cfg.music_mode if cfg.music_mode != "native" else "spectrum"
        self._choose_mode(strip_mode, initial=True)
        self._fps_t = time.perf_counter()
        self._populate_audio()
        self._on_source(self.PC, user=False)

    # -- sources
    def show_source(self, index: int) -> None:
        """Selects a tab without starting or stopping anything (used when syncing with the controller)."""
        self.source.set_index(index)
        self._on_source(index, user=False)

    def _on_source(self, index: int, user: bool = True) -> None:
        self._show_stack_page(1 if index == self.STRIP else 0)
        pc = index == self.PC
        self.pc_box.setVisible(pc)
        self.source_note.setText(
            "Your PC audio is analyzed here and streamed to the controller, which animates the effect LED by LED."
            if pc else
            "The controller listens to the room with its own microphone: no latency, the PC can be turned off.")
        if user and index in (self.PC, self.MIC):
            # Like the app: switching the source immediately plays the current music effect from it.
            effect = self._current_sound_effect()
            if index == self.PC:
                self.session.play_pc_effect(effect)
            elif self.session.engine.running or self.session.state.audio_input == 1:
                self.session.play_mic_effect(effect)
        self._refresh_running()

    def _show_stack_page(self, i: int) -> None:
        # A QStackedWidget is as tall as its tallest page: ignore the hidden page's size so the strip
        # tab is not stretched to the height of the 18-effect grid.
        for j in range(self.stack.count()):
            page = self.stack.widget(j)
            policy = QSizePolicy.Preferred if j == i else QSizePolicy.Ignored
            page.setSizePolicy(policy, policy)
        self.stack.setCurrentIndex(i)
        self.stack.adjustSize()

    def _current_sound_effect(self) -> int:
        p, st = self.session.protocol, self.session.state
        eff = p.effect(st.effect) if p else None
        return st.effect if eff is not None and eff.kind == "sound" else self.session.config.music_native_effect

    def _play_effect(self, effect_id: int) -> None:
        if self.source.index() == self.PC:
            self.session.play_pc_effect(effect_id)
        else:
            self.session.play_mic_effect(effect_id)
        self._show_effect_settings(effect_id)

    def _show_effect_settings(self, effect_id: int | None) -> None:
        p = self.session.protocol
        eff = p.effect(effect_id) if p else None
        self.fx_color_box.setVisible(bool(eff and eff.colorable))
        self.fx_length.setVisible(bool(eff and eff.has_length and p.max_length))

    def _toggle_pc_effects(self) -> None:
        engine = self.session.engine
        if engine.running and engine.visualizer.native_feed:
            self.session.stop_pc_music()
        else:
            self.session.play_pc_effect(self.session.config.music_native_effect)

    def _toggle_strip(self) -> None:
        engine = self.session.engine
        if engine.running and not engine.visualizer.native_feed:
            self.session.stop_pc_music()
        else:
            self.session.switch_visualizer(self._strip_mode)
            if not engine.running:
                self.session.start_pc_music()

    # -- PC audio
    def _populate_audio(self):
        self.audio_combo.blockSignals(True)
        self.audio_combo.clear()
        if not capture.available():
            self.audio_combo.addItem("Audio capture is not available on this system", None)
            self.audio_combo.setEnabled(False)
        else:
            self.audio_combo.addItem("🔊 Default Windows audio output", None)
            for d in capture.list_devices():
                self.audio_combo.addItem(d.label, d)
                if d.name == self.session.config.music_device:
                    self.audio_combo.setCurrentIndex(self.audio_combo.count() - 1)
                    self.session.engine.device = d
        self.audio_combo.blockSignals(False)

    def _set_audio_device(self, _):
        dev = self.audio_combo.currentData()
        self.session.engine.device = dev
        self.session.config.music_device = dev.name if dev else ""
        self.session.config.save()
        if self.session.engine.running:  # restart on the new source
            self.session.engine.stop()
            self.session.start_pc_music()

    def _set_engine(self, attr, value, cfg_key):
        setattr(self.session.engine, attr, value)
        setattr(self.session.config, cfg_key, value)
        self._save_later()

    def _set_fps(self, v: int, other: SliderRow) -> None:
        other.set_value(v)  # the PC audio and strip tabs share the same frame rate
        self._set_engine("fps", v, "music_fps")

    def _set_floor(self, v):
        self.session.engine.params.floor = v / 100
        self.session.config.music_floor = v / 100
        self._save_later()

    def _set_music_color(self, rgb):
        self.session.engine.params.color = rgb
        self.session.config.music_color = rgb
        self._save_later()

    def _save_later(self):
        if not hasattr(self, "_save_timer"):
            self._save_timer = QTimer(self, singleShot=True, interval=800)
            self._save_timer.timeout.connect(self.session.config.save)
        self._save_timer.start()

    def _choose_mode(self, vis_id: str, initial: bool = False):
        cls = next((v for v in VISUALIZERS if v.id == vis_id and not v.native_feed),
                   next(v for v in VISUALIZERS if not v.native_feed))
        self._strip_mode = cls.id
        _select_card(self.mode_group, cls.id)
        self.mode_desc.setText(f"{cls.name} — {cls.description}")
        self.color_box.setVisible(cls.uses_color)
        self.anim_box.setVisible(cls.brightness_only)
        if initial:
            self.session.engine.set_visualizer(cls.id)
            return
        engine = self.session.engine
        if engine.running and not engine.visualizer.native_feed:
            self.session.switch_visualizer(cls.id)  # live switch between strip modes
        self.session.config.music_mode = cls.id
        self.session.config.save()

    def _on_running(self, running: bool):
        if not running:
            self.spectrum.clear()
            self.strip_spectrum.clear()
            self.fps_label.setText("")
        self._refresh_running()

    def _refresh_running(self):
        engine = self.session.engine
        native = engine.running and engine.visualizer.native_feed
        strip = engine.running and not engine.visualizer.native_feed
        for btn, on in ((self.btn_start, native), (self.btn_strip, strip)):
            btn.setObjectName("danger" if on else "primary")
            set_icon_button(btn, "stop" if on else "play", "Stop" if on else "Start")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        mic = self.source.index() == self.MIC
        self.live_title.setText("Microphone" if mic else "Live" if native else "Stopped")
        self.live_title.setStyleSheet(f"color: {theme.SUCCESS};" if native or mic else "")
        self.strip_title.setText("Live" if strip else "Stopped")
        self.strip_title.setStyleSheet(f"color: {theme.SUCCESS};" if strip else "")

    def _on_frame(self, frame, rgb, level):
        view = self.spectrum if self.session.engine.visualizer.native_feed else self.strip_spectrum
        view.push(frame.bands, rgb, level, frame.beat)
        now = time.perf_counter()
        if now - self._fps_t >= 1.0:
            self.fps_label.setText(f"sending at {self.session.engine.fps} fps")
            self._fps_t = now

    # -- shared
    def set_protocol(self, protocol):
        super().set_protocol(protocol)
        if protocol is None:
            return
        sound = protocol.sound_effects()
        self.fx_grid.set_effects(sound)
        has_mic = bool(sound)
        feed = protocol.supports_audio_feed
        self.source.group.button(self.PC).setEnabled(feed)
        self.source.group.button(self.MIC).setEnabled(has_mic)
        if not has_mic:
            self.show_source(self.STRIP)
        elif not feed and self.source.index() == self.PC:
            self.show_source(self.MIC)
        self.fx_sens.set_range(1, protocol.max_sensitivity)
        if protocol.max_length:
            self.fx_length.set_range(1, protocol.max_length)
        self.anim_combo.blockSignals(True)
        self.anim_combo.clear()
        for e in protocol.dynamic_effects():
            self.anim_combo.addItem(e.name, e.id)
        idx = self.anim_combo.findData(self.session.config.music_animated_effect)
        self.anim_combo.setCurrentIndex(max(0, idx))
        self.anim_combo.blockSignals(False)
        if idx < 0 and self.anim_combo.count():
            self.session.config.music_animated_effect = self.anim_combo.itemData(0)
        self._show_effect_settings(self.session.config.music_native_effect)

    def apply_state(self, st: DeviceState) -> None:
        p = self.session.protocol
        if st.effect is not None:
            playing = st.light_mode in (0, None) and p is not None and (eff := p.effect(st.effect)) and eff.kind == "sound"
            self.fx_grid.select(st.effect if playing else None)
            if playing:
                self._show_effect_settings(st.effect)
        if st.sensitivity and not self.fx_sens.slider.isSliderDown():
            self.fx_sens.set_value(st.sensitivity)
        if st.length and not self.fx_length.slider.isSliderDown():
            self.fx_length.set_value(st.length)
        if st.rgb:
            self.fx_color.set_color(st.rgb)


# ============================================================================ Settings
class SettingsPage(Page):
    def __init__(self, session: Session):
        super().__init__("Settings", "Controller configuration and advanced tools.", needs_device=False)
        self.session = session

        # --- LED strip
        box = QVBoxLayout()
        box.addWidget(label("LED strip", "sectionTitle"))
        r = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(label("Color order (RGB)"))
        col.addWidget(label("If red shows up as green (or similar), change the order until the colors "
                            "are right.", "small", wrap=True))
        r.addLayout(col, 1)
        self.chip = QComboBox()
        self.chip.setMinimumWidth(140)
        r.addWidget(self.chip)
        box.addLayout(r)
        self.chip_row = r
        self.strip_card = card(box)
        self.body_layout.addWidget(self.strip_card)

        # --- performance
        perf = QVBoxLayout()
        perf.addWidget(label("Transmission", "sectionTitle"))
        self.interval = SliderRow("Minimum interval between writes", 40, 100, session.config.write_interval_ms,
                                  lambda v: f"{v} ms  (≈ {1000 // v} writes/s)")
        perf.addWidget(self.interval)
        perf.addWidget(label("Lower = smoother. If some commands seem to be ignored or the connection is unstable, "
                             "increase this value.", "small", wrap=True))
        self.body_layout.addWidget(card(perf))

        # --- console
        con = QVBoxLayout()
        head = QHBoxLayout()
        head.addWidget(label("Protocol console", "sectionTitle"))
        head.addStretch()
        self.btn_query = QPushButton("Read state")
        self.btn_query.clicked.connect(session.refresh)
        head.addWidget(self.btn_query)
        con.addLayout(head)
        con.addWidget(label("Send raw bytes (hexadecimal) and watch the controller's replies. "
                            "Useful to discover new commands — see also tools/probe.py.", "small", wrap=True))
        send_row = QHBoxLayout()
        self.raw = QLineEdit()
        self.raw.setPlaceholderText("e.g. A0 70 00")
        send_row.addWidget(self.raw, 1)
        self.btn_send = QPushButton("Send")
        self.btn_send.setObjectName("primary")
        send_row.addWidget(self.btn_send)
        con.addLayout(send_row)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(400)
        self.log.setMinimumHeight(180)
        con.addWidget(self.log)
        self.state_label = label("", "small", wrap=True)
        self.state_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        con.addWidget(self.state_label)
        self.console_card = card(con)
        self.body_layout.addWidget(self.console_card)

        # --- about
        about = QVBoxLayout()
        about.addWidget(label("About", "sectionTitle"))
        models = ", ".join(m.name for m in MODELS)
        about.addWidget(label(f"LED Controller 2.0 — supported BanlanX controllers: {models}. "
                              "Unknown but compatible controllers are detected by probing their protocol.",
                              "muted", wrap=True))
        self.body_layout.addWidget(card(about))

        self.chip.currentIndexChanged.connect(lambda i: i >= 0 and self.chip.isEnabled() and session.set_chip_order(i))
        self.interval.changed.connect(self._set_interval)
        self.btn_send.clicked.connect(self._send_raw)
        self.raw.returnPressed.connect(self._send_raw)
        session.ble.notification.connect(self._on_notification)  # bound method: runs in the UI thread
        self.set_protocol(None)

    def _set_interval(self, v):
        self.session.config.write_interval_ms = v
        self.session.ble.min_interval = v / 1000
        self.session.config.save()

    def _send_raw(self):
        text = self.raw.text().replace("0x", "").replace(",", " ")
        try:
            data = bytes.fromhex(text)
        except ValueError:
            self.log.appendPlainText("⚠ Invalid hexadecimal")
            return
        if not data:
            return
        self.session.ble.send(data)
        self._log("→", data)

    def _on_notification(self, data: bytes):
        self._log("←", data)

    def _log(self, arrow, data: bytes):
        self.log.appendPlainText(f"{time.strftime('%H:%M:%S')}  {arrow}  {data.hex(' ').upper()}")

    def set_protocol(self, protocol):
        super().set_protocol(protocol)
        connected = protocol is not None
        self.console_card.setEnabled(connected)
        self.strip_card.setEnabled(connected)
        self.chip.blockSignals(True)
        self.chip.clear()
        if connected and protocol.supports_chip_order:
            self.chip.addItems(chip_orders(protocol.model.colors))
            self.chip.setEnabled(True)
        else:
            self.chip.addItem("Not available")
            self.chip.setEnabled(False)
        self.chip.blockSignals(False)

    def apply_state(self, st: DeviceState) -> None:
        if st.chip_order is not None and self.chip.isEnabled():
            self.chip.blockSignals(True)
            self.chip.setCurrentIndex(st.chip_order)
            self.chip.blockSignals(False)
        if st.raw:
            self.state_label.setText(f"Last state received: {st.raw.hex(' ').upper()}")
