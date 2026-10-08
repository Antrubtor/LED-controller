"""Custom UI widgets."""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QConicalGradient,
    QIcon,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from . import theme


# ---------------------------------------------------------------------------- layout
class FlowLayout(QLayout):
    """Lays widgets out in rows that wrap according to the available width."""

    def __init__(self, parent=None, spacing: int = 10, fill_min_width: int | None = None):
        super().__init__(parent)
        self._items = []
        self._spacing = spacing
        self._fill = fill_min_width  # when set: grid whose cards stretch to fill the row
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._layout(QRect(0, 0, width, 0), dry=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._layout(rect, dry=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _layout(self, rect, dry):
        items = [it for it in self._items if it.widget() is None or not it.widget().isHidden()]
        if self._fill:
            sp = self._spacing
            cols = max(1, (rect.width() + sp) // (self._fill + sp))
            w = (rect.width() - sp * (cols - 1)) / cols
            y, line_h = rect.y(), 0
            for n, item in enumerate(items):
                col = n % cols
                if col == 0 and n:
                    y += line_h + sp
                    line_h = 0
                h = item.sizeHint().height()
                if not dry:
                    x = rect.x() + round(col * (w + sp))
                    item.setGeometry(QRect(x, y, round(w), h))
                line_h = max(line_h, h)
            return y + line_h - rect.y()
        x, y, line_h = rect.x(), rect.y(), 0
        for item in items:
            hint = item.sizeHint()
            if x + hint.width() > rect.right() + 1 and line_h > 0:
                x, y, line_h = rect.x(), y + line_h + self._spacing, 0
            if not dry:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y()


def card(layout: QLayout | None = None, name: str = "card") -> QFrame:
    frame = QFrame()
    frame.setObjectName(name)
    if layout is not None:
        frame.setLayout(layout)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)
    return frame


def label(text: str, name: str | None = None, wrap: bool = False) -> QLabel:
    lbl = QLabel(text)
    if name:
        lbl.setObjectName(name)
    lbl.setWordWrap(wrap)
    return lbl


def icon_label(glyph: str, size: int = 16, color: str = theme.TEXT) -> QLabel:
    lbl = QLabel(theme.ICONS.get(glyph, glyph))
    lbl.setFont(theme.icon_font(size))
    lbl.setStyleSheet(f"color: {color};")
    return lbl


def glyph_icon(glyph: str, color: str, size: int = 16, color_on: str | None = None) -> QIcon:
    """Icon drawn from a font glyph (optionally with another color when the button is checked)."""
    icon = QIcon()
    for state, col in ((QIcon.Off, color), (QIcon.On, color_on or color)):
        pm = QPixmap(size * 2, size * 2)
        pm.setDevicePixelRatio(2)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.setFont(theme.icon_font(size))
        p.setPen(QColor(col))
        p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, theme.ICONS.get(glyph, glyph))
        p.end()
        icon.addPixmap(pm, QIcon.Normal, state)
    return icon


_ICON_COLORS = {"primary": "#ffffff", "danger": theme.DANGER}


def icon_button(glyph: str, text: str = "", name: str | None = None) -> QPushButton:
    btn = QPushButton(f"  {text}" if text else "")
    if name:
        btn.setObjectName(name)
    btn._glyph = glyph
    btn.setIcon(glyph_icon(glyph, _ICON_COLORS.get(name or "", theme.TEXT)))
    btn.setIconSize(QSize(16, 16))
    btn.setCursor(Qt.PointingHandCursor)
    btn.setMinimumHeight(38)
    return btn


def set_icon_button(btn: QPushButton, glyph: str | None = None, text: str | None = None) -> None:
    if glyph is not None:
        btn._glyph = glyph
    btn.setIcon(glyph_icon(btn._glyph, _ICON_COLORS.get(btn.objectName(), theme.TEXT)))
    if text is not None:
        btn.setText(f"  {text}")


# ---------------------------------------------------------------------------- controls
class Toggle(QAbstractButton):
    """Windows 11 style switch."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(44, 24)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        on = self.isChecked()
        enabled = self.isEnabled()
        p.setPen(QPen(QColor(theme.ACCENT if on else "#4a5163"), 1.2))
        p.setBrush(QColor(theme.ACCENT if on else theme.SURFACE_2) if enabled else QColor(theme.SURFACE))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 8
        x = r.right() - d - 4 if on else r.left() + 4
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("white" if on else "#aab0c0"))
        p.drawEllipse(QRectF(x, r.top() + 4, d, d))


class SliderRow(QWidget):
    """Label + value + slider. `changed` while dragging, `released` on release."""

    changed = Signal(int)
    released = Signal(int)

    def __init__(self, title: str, lo: int, hi: int, value: int, fmt=None, parent=None):
        super().__init__(parent)
        self._fmt = fmt or (lambda v: str(v))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        top = QHBoxLayout()
        self.title = QLabel(title)
        self.title.setStyleSheet("font-weight: 600;")
        self.value_label = QLabel(self._fmt(value))
        self.value_label.setObjectName("muted")
        top.addWidget(self.title)
        top.addStretch()
        top.addWidget(self.value_label)
        lay.addLayout(top)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(lo, hi)
        self.slider.setValue(value)
        self.slider.setCursor(Qt.PointingHandCursor)
        lay.addWidget(self.slider)
        self.slider.valueChanged.connect(self._on_change)
        self.slider.sliderReleased.connect(lambda: self.released.emit(self.slider.value()))

    def _on_change(self, v: int):
        self.value_label.setText(self._fmt(v))
        self.changed.emit(v)

    def value(self) -> int:
        return self.slider.value()

    def set_value(self, v: int, silent: bool = True):
        if silent:
            self.slider.blockSignals(True)
        self.slider.setValue(v)
        self.value_label.setText(self._fmt(self.slider.value()))
        self.slider.blockSignals(False)

    def set_range(self, lo: int, hi: int):
        self.slider.blockSignals(True)
        self.slider.setRange(lo, hi)  # also clamps the current value
        self.slider.blockSignals(False)
        self.value_label.setText(self._fmt(self.slider.value()))


class Segmented(QFrame):
    changed = Signal(int)

    def __init__(self, options: list[str], parent=None):
        super().__init__(parent)
        self.setObjectName("segmentBar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)
        self.group = QButtonGroup(self)
        for i, text in enumerate(options):
            b = QPushButton(text)
            b.setObjectName("segment")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            self.group.addButton(b, i)
            lay.addWidget(b)
        self.group.button(0).setChecked(True)
        self.group.idClicked.connect(self.changed)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)

    def index(self) -> int:
        return self.group.checkedId()

    def set_index(self, i: int):
        self.group.button(i).setChecked(True)


# ---------------------------------------------------------------------------- effect cards
def _gradient(rect: QRectF, colors: tuple[str, ...]) -> QLinearGradient:
    g = QLinearGradient(rect.topLeft(), rect.topRight())
    if len(colors) == 1:
        colors = (colors[0], colors[0])
    for i, c in enumerate(colors):
        g.setColorAt(i / (len(colors) - 1), QColor(c))
    return g


class EffectCard(QAbstractButton):
    """Clickable card with a gradient preview of the effect colors."""

    def __init__(self, key, title: str, colors: tuple[str, ...], subtitle: str = "", width: int = 168, parent=None):
        super().__init__(parent)
        self.key = key
        self.title = title
        self.subtitle = subtitle
        self.colors = colors
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(title if not subtitle else f"{title}\n{subtitle}")
        self._w = width
        self._hover = False
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def sizeHint(self):
        return QSize(self._w, 84 if not self.subtitle else 104)

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        selected = self.isChecked()
        bg = theme.SURFACE_2 if (self._hover or selected) else theme.SURFACE
        p.setPen(QPen(QColor(theme.ACCENT if selected else theme.BORDER), 2 if selected else 1))
        p.setBrush(QColor(bg))
        p.drawRoundedRect(r, 12, 12)

        bar = QRectF(r.left() + 10, r.top() + 10, r.width() - 20, 30)
        path = QPainterPath()
        path.addRoundedRect(bar, 8, 8)
        p.fillPath(path, QBrush(_gradient(bar, self.colors)))
        if selected:
            glow = QRadialGradient(bar.center(), bar.width() / 1.4)
            glow.setColorAt(0, QColor(255, 255, 255, 40))
            glow.setColorAt(1, QColor(255, 255, 255, 0))
            p.fillPath(path, glow)

        p.setPen(QColor(theme.TEXT if self.isEnabled() else theme.MUTED))
        f = self.font()
        f.setWeight(f.Weight.DemiBold)
        p.setFont(f)
        text_rect = QRectF(r.left() + 12, bar.bottom() + 6, r.width() - 24, 36)
        p.drawText(text_rect, Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, self.title)
        if self.subtitle:
            f.setWeight(f.Weight.Normal)
            f.setPointSizeF(f.pointSizeF() * 0.88)
            p.setFont(f)
            p.setPen(QColor(theme.MUTED))
            p.drawText(QRectF(r.left() + 12, r.bottom() - 26, r.width() - 24, 20), Qt.AlignLeft | Qt.AlignVCenter,
                       self.subtitle)


# ---------------------------------------------------------------------------- color
class ColorWheel(QWidget):
    """Hue/saturation wheel. `colorChanged` while dragging, `colorCommitted` on release."""

    colorChanged = Signal(QColor)
    colorCommitted = Signal(QColor)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._h, self._s, self._v = 0.7, 0.65, 1.0
        self._cache: QImage | None = None
        self.setMinimumSize(240, 240)
        self.setCursor(Qt.CrossCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def heightForWidth(self, w):
        return w

    def color(self) -> QColor:
        return QColor.fromHsvF(self._h, self._s, self._v)

    def set_color(self, c: QColor):
        h, s, v, _ = c.getHsvF()
        if h >= 0:
            self._h = h
        self._s, self._v = s, max(v, 0.05)
        self.update()

    def set_value(self, v: float):
        self._v = v
        self._cache = None
        self.update()

    def _geometry(self):
        side = min(self.width(), self.height()) - 16
        return QPointF(self.width() / 2, self.height() / 2), side / 2

    def _render_wheel(self, radius: float) -> QImage:
        size = int(radius * 2)
        img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        c = QPointF(radius, radius)
        cg = QConicalGradient(c, 0)
        for i in range(13):
            cg.setColorAt(i / 12, QColor.fromHsvF((i / 12) % 1.0, 1, self._v))
        p.setPen(Qt.NoPen)
        p.setBrush(cg)
        p.drawEllipse(c, radius, radius)
        rg = QRadialGradient(c, radius)
        white = QColor.fromHsvF(0, 0, self._v)
        rg.setColorAt(0, white)
        transparent = QColor(white)
        transparent.setAlpha(0)
        rg.setColorAt(1, transparent)
        p.setBrush(rg)
        p.drawEllipse(c, radius, radius)
        p.end()
        return img

    def paintEvent(self, _):
        center, radius = self._geometry()
        if self._cache is None or self._cache.width() != int(radius * 2):
            self._cache = self._render_wheel(radius)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.drawImage(QPointF(center.x() - radius, center.y() - radius), self._cache)
        # Qt's conical gradient runs counter-clockwise, starting on the right.
        ang = self._h * 2 * math.pi
        pos = QPointF(center.x() + math.cos(ang) * self._s * radius, center.y() - math.sin(ang) * self._s * radius)
        p.setPen(QPen(QColor("white"), 3))
        p.setBrush(self.color())
        p.drawEllipse(pos, 11, 11)
        p.setPen(QPen(QColor(0, 0, 0, 90), 1))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(pos, 13, 13)

    def _pick(self, pt: QPointF):
        center, radius = self._geometry()
        dx, dy = pt.x() - center.x(), center.y() - pt.y()
        self._h = (math.atan2(dy, dx) / (2 * math.pi)) % 1.0
        self._s = min(1.0, math.hypot(dx, dy) / radius)
        self.update()
        self.colorChanged.emit(self.color())

    def mousePressEvent(self, e):
        self._pick(e.position())

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            self._pick(e.position())

    def mouseReleaseEvent(self, e):
        self.colorCommitted.emit(self.color())


class Swatch(QAbstractButton):
    def __init__(self, color: QColor, size: int = 34, parent=None):
        super().__init__(parent)
        self.color = QColor(color)
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(self.color.name().upper())

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        p.setPen(QPen(QColor(255, 255, 255, 140 if self.underMouse() else 40), 2))
        p.setBrush(self.color)
        p.drawEllipse(r)


class ColorPreview(QWidget):
    """Large glowing dot that mimics how the LED strip looks."""

    def __init__(self, size: int = 120, parent=None):
        super().__init__(parent)
        self._color = QColor(theme.ACCENT)
        self._level = 1.0
        self.setMinimumSize(size, size)

    def set_color(self, c: QColor, level: float = 1.0):
        self._color, self._level = QColor(c), max(0.0, min(1.0, level))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QColor(self._color)
        c.setRgbF(c.redF() * self._level, c.greenF() * self._level, c.blueF() * self._level)
        center = QPointF(self.width() / 2, self.height() / 2)
        radius = min(self.width(), self.height()) / 2
        glow = QRadialGradient(center, radius)
        g0 = QColor(c)
        g0.setAlpha(int(200 * self._level))
        glow.setColorAt(0.35, g0)
        g1 = QColor(c)
        g1.setAlpha(0)
        glow.setColorAt(1.0, g1)
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(center, radius, radius)
        p.setBrush(c)
        p.setPen(QPen(QColor(255, 255, 255, 50), 2))
        p.drawEllipse(center, radius * 0.42, radius * 0.42)


class SpectrumView(QWidget):
    """Spectrum bars + simulated LED strip for the music mode."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bands = np.zeros(24)
        self._color = QColor(theme.ACCENT)
        self._level = 0.0
        self._beat = 0.0
        self.setFixedHeight(200)

    def push(self, bands: np.ndarray, rgb, level: float, beat: bool):
        if len(bands):
            self._bands = bands
        self._color = QColor(*rgb)
        self._level = level
        self._beat = 1.0 if beat else self._beat * 0.8
        self.update()

    def clear(self):
        self._bands = np.zeros_like(self._bands)
        self._level = 0
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        strip = QRectF(r.left(), r.bottom() - 22, r.width(), 14)
        bars_area = QRectF(r.left(), r.top() + 4, r.width(), strip.top() - r.top() - 18)

        n = len(self._bands)
        gap = 4
        w = (bars_area.width() - gap * (n - 1)) / max(n, 1)
        for i, v in enumerate(self._bands):
            h = max(3.0, float(v) * bars_area.height())
            rect = QRectF(bars_area.left() + i * (w + gap), bars_area.bottom() - h, w, h)
            g = QLinearGradient(rect.bottomLeft(), rect.topLeft())
            hue = 0.75 - 0.75 * i / max(n - 1, 1)
            g.setColorAt(0, QColor.fromHsvF(hue, 0.85, 0.55))
            g.setColorAt(1, QColor.fromHsvF(hue, 0.7, 1.0))
            p.setPen(Qt.NoPen)
            p.setBrush(g)
            p.drawRoundedRect(rect, 3, 3)

        c = QColor(self._color)
        lv = self._level
        c.setRgbF(c.redF() * lv, c.greenF() * lv, c.blueF() * lv)
        glow = QColor(c)
        glow.setAlpha(int(110 * lv))
        p.setBrush(glow)
        p.drawRoundedRect(strip.adjusted(-4, -6, 4, 6), 12, 12)
        p.setBrush(c)
        p.setPen(QPen(QColor(255, 255, 255, 30), 1))
        p.drawRoundedRect(strip, 7, 7)
