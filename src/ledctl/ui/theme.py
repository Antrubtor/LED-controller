"""Dark application theme (palette, Qt stylesheet, Segoe Fluent icons)."""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QFontDatabase

BG = "#0e1016"
SIDEBAR = "#13161e"
SURFACE = "#181c26"
SURFACE_2 = "#1f2431"
SURFACE_3 = "#272d3c"
BORDER = "#2a3040"
TEXT = "#e9ebf2"
MUTED = "#8a91a5"
ACCENT = "#7c5cff"
ACCENT_HOVER = "#9077ff"
ACCENT_SOFT = "#2a2350"
SUCCESS = "#22c55e"
WARNING = "#f59e0b"
DANGER = "#ef4444"

# Glyphs of the Windows 11 icon font (falls back to Segoe MDL2 Assets on Windows 10).
ICONS = {
    "bluetooth": "",
    "color": "",
    "effects": "",
    "music": "",
    "settings": "",
    "power": "",
    "brightness": "",
    "refresh": "",
    "search": "",
    "play": "",
    "stop": "",
    "link": "",
    "unlink": "",
    "check": "",
    "mic": "",
    "speaker": "",
    "info": "",
    "send": "",
}


def icon_font(size: int = 14) -> QFont:
    families = QFontDatabase.families()
    family = "Segoe Fluent Icons" if "Segoe Fluent Icons" in families else "Segoe MDL2 Assets"
    f = QFont(family)
    f.setPixelSize(size)
    return f


def ui_font() -> QFont:
    families = QFontDatabase.families()
    family = next((f for f in ("Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI", "Inter") if f in families), "")
    f = QFont(family)
    f.setPointSizeF(9.5)
    return f


def qcolor(hex_: str, alpha: int = 255) -> QColor:
    c = QColor(hex_)
    c.setAlpha(alpha)
    return c


STYLESHEET = f"""
* {{ outline: none; }}
QWidget {{ color: {TEXT}; background: transparent; }}
QMainWindow, #root {{ background: {BG}; }}
QToolTip {{ background: {SURFACE_3}; color: {TEXT}; border: 1px solid {BORDER}; padding: 6px 8px; border-radius: 6px; }}

#sidebar {{ background: {SIDEBAR}; border-right: 1px solid {BORDER}; }}
#appTitle {{ font-size: 15pt; font-weight: 700; }}
#appSubtitle {{ color: {MUTED}; font-size: 8.5pt; }}

QPushButton#nav {{
    text-align: left; padding: 10px 14px; border-radius: 10px; border: none; background: transparent;
    color: {MUTED}; font-size: 10.5pt; font-weight: 600;
}}
QPushButton#nav:hover {{ background: {SURFACE_2}; color: {TEXT}; }}
QPushButton#nav:checked {{ background: {ACCENT_SOFT}; color: {TEXT}; }}

#pageTitle {{ font-size: 20pt; font-weight: 700; }}
#pageSubtitle {{ color: {MUTED}; font-size: 10pt; }}
#sectionTitle {{ font-size: 11pt; font-weight: 700; }}
#muted {{ color: {MUTED}; }}
#small {{ color: {MUTED}; font-size: 8.5pt; }}

#card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 14px; }}
#banner {{ background: {ACCENT_SOFT}; border: 1px solid {ACCENT}; border-radius: 12px; }}
#errorBanner {{ background: #3a1620; border: 1px solid {DANGER}; border-radius: 12px; }}

QPushButton {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 9px;
    padding: 8px 14px; font-weight: 600;
}}
QPushButton:hover {{ background: {SURFACE_3}; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:disabled {{ color: #5a6072; background: {SURFACE}; }}
QPushButton#primary {{ background: {ACCENT}; border: 1px solid {ACCENT}; color: white; }}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#primary:disabled {{ background: {ACCENT_SOFT}; border-color: {ACCENT_SOFT}; color: {MUTED}; }}
QPushButton#danger {{ background: transparent; border: 1px solid {DANGER}; color: {DANGER}; }}
QPushButton#danger:hover {{ background: #3a1620; }}
QPushButton#power {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 12px; color: {MUTED};
    font-size: 10.5pt; font-weight: 700; padding: 10px 14px;
}}
QPushButton#power:hover {{ border-color: {SUCCESS}; color: {TEXT}; }}
QPushButton#power:checked {{ background: #14532d; border-color: {SUCCESS}; color: white; }}
QPushButton#power:checked:hover {{ background: #166534; }}
QPushButton#power:disabled {{ background: {SURFACE}; border-color: {BORDER}; color: #5a6072; }}
QPushButton#chip {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 14px; padding: 5px 12px;
    color: {MUTED}; font-weight: 600;
}}
QPushButton#chip:hover {{ color: {TEXT}; }}
QPushButton#chip:checked {{ background: {ACCENT_SOFT}; border-color: {ACCENT}; color: {TEXT}; }}
QPushButton#segment {{ border-radius: 8px; border: none; background: transparent; color: {MUTED}; padding: 8px 18px; }}
QPushButton#segment:checked {{ background: {SURFACE_3}; color: {TEXT}; }}
#segmentBar {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 11px; }}

QLineEdit, QComboBox, QSpinBox {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 9px; padding: 7px 10px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; selection-background-color: {ACCENT_SOFT};
    padding: 4px; outline: none;
}}

QSlider::groove:horizontal {{ height: 6px; background: {SURFACE_3}; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 3px; }}
QSlider::handle:horizontal {{
    background: white; width: 16px; height: 16px; margin: -6px 0; border-radius: 8px; border: 2px solid {ACCENT};
}}
QSlider::handle:horizontal:hover {{ background: #f0ecff; }}
QSlider:disabled::sub-page:horizontal {{ background: #4a4466; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {SURFACE_3}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #3a4255; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ height: 0; background: none; }}

QPlainTextEdit {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px; padding: 6px;
    font-family: Consolas, 'Cascadia Mono', monospace; font-size: 9pt;
}}
"""
