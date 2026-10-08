"""Persistent settings, stored in the user's config folder."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

log = logging.getLogger(__name__)


def config_dir() -> Path:
    base = os.environ.get("APPDATA") or os.path.join(Path.home(), ".config")
    return Path(base) / "LED-Controller"


CONFIG_PATH = config_dir() / "settings.json"


@dataclass
class Config:
    last_address: str = ""
    last_name: str = ""
    last_model: str = ""
    auto_connect: bool = True
    auto_reconnect: bool = True
    write_interval_ms: int = 50

    color: tuple[int, int, int] = (124, 92, 255)
    brightness: int = 255
    custom_colors: list[str] = field(default_factory=list)

    music_mode: str = "spectrum"
    music_color: tuple[int, int, int] = (124, 92, 255)
    music_sensitivity: float = 1.0
    music_smoothing: float = 0.15
    music_fps: int = 20
    music_floor: float = 0.04
    music_device: str = ""  # audio device name; empty = default output
    music_animated_effect: int = 0x01
    music_native_effect: int = 0xC9  # Full Color Rhythm Spectrum

    screen_monitor: int = 0
    screen_area: str = "full"  # "full" or "edges"
    screen_boost: float = 1.3
    screen_smoothing: float = 0.3
    screen_floor: float = 0.05
    screen_fps: int = 15

    def save(self) -> None:
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = CONFIG_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(CONFIG_PATH)
        except OSError:
            log.exception("Could not save the settings")

    @classmethod
    def load(cls) -> Config:
        cfg = cls()
        data: dict = {}
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                log.warning("Unreadable settings, using defaults")
        if data.get("write_interval_ms", 50) < 40:
            data["write_interval_ms"] = 50  # older versions defaulted to 20 ms, which floods the link
        if data.get("music_fps", 20) > 30:
            data["music_fps"] = 20
        known = {f.name: f for f in fields(cls)}
        for key, value in data.items():
            if key in known:
                default = getattr(cfg, key)
                if isinstance(default, tuple) and isinstance(value, list):
                    value = tuple(value)
                if default is None or isinstance(value, type(default)) or (isinstance(default, float) and isinstance(value, int)):
                    setattr(cfg, key, value)
        return cfg
