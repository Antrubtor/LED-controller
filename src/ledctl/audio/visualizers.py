"""PC-driven music modes: each visualizer turns an AudioFrame into a color + brightness."""

from __future__ import annotations

import colorsys
import math
import random
from dataclasses import dataclass

from .analyzer import AudioFrame


@dataclass
class VisualParams:
    color: tuple[int, int, int] = (124, 92, 255)
    floor: float = 0.04  # minimum brightness (0..1)


def _hsv(h: float, s: float = 1.0, v: float = 1.0) -> tuple[int, int, int]:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return round(r * 255), round(g * 255), round(b * 255)


class Visualizer:
    id = ""
    name = ""
    description = ""
    uses_color = False
    brightness_only = False  # drives the brightness only (an animated controller effect runs underneath)
    preview: tuple[str, ...] = ()
    speed: int | None = None  # optional controller effect speed (brightness-only visualizers)
    native_feed = False  # streams spectrum levels to the controller's own music effects instead of a color

    def render(self, f: AudioFrame, dt: float, p: VisualParams) -> tuple[tuple[int, int, int], float]:
        raise NotImplementedError

    @staticmethod
    def _lvl(x: float, p: VisualParams) -> float:
        return p.floor + (1.0 - p.floor) * min(1.0, max(0.0, x))


class Spectrum(Visualizer):
    id, name = "spectrum", "Spectrum"
    description = "The color follows the dominant frequencies: red for bass, blue/purple for treble."
    preview = ("#ff2a2a", "#ffd21f", "#22e05a", "#2f6bff", "#b53dff")

    def render(self, f, dt, p):
        return _hsv(f.centroid * 0.8), self._lvl(f.level, p)


class RgbBands(Visualizer):
    id, name = "rgb", "Bass / Mid / Treble"
    description = "Red = bass, green = mids, blue = treble, mixed in real time."
    preview = ("#ff2a2a", "#22e05a", "#2f6bff")

    def render(self, f, dt, p):
        r, g, b = f.bass**1.5, f.mid**1.5, f.treble**1.5
        m = max(r, g, b, 1e-6)
        return (round(255 * r / m), round(255 * g / m), round(255 * b / m)), self._lvl(m, p)


class Pulse(Visualizer):
    id, name = "pulse", "Pulse"
    description = "Your color, with its intensity following the volume."
    uses_color = True
    preview = ("#000000", "#7c5cff", "#000000")

    def render(self, f, dt, p):
        return p.color, self._lvl(f.level**1.3, p)


class Bass(Visualizer):
    id, name = "bass", "Bass"
    description = "Only reacts to kicks and bass: great for electro and hip-hop."
    uses_color = True
    preview = ("#000000", "#ff2a4a", "#000000", "#ff2a4a")

    def render(self, f, dt, p):
        x = max(0.0, (f.bass - 0.25) / 0.75)
        return p.color, self._lvl(x**1.6, p)


class BeatFlash(Visualizer):
    id, name = "beat", "Beat Flash"
    description = "Changes color on every beat with a fading flash."
    preview = ("#ff2a2a", "#000000", "#22e05a", "#000000", "#2f6bff")

    def __init__(self):
        self.hue = random.random()
        self.flash = 0.0

    def render(self, f, dt, p):
        if f.beat:
            self.hue = (self.hue + 0.618034) % 1.0
            self.flash = 1.0
        self.flash *= math.exp(-dt / 0.22)
        return _hsv(self.hue), self._lvl(max(self.flash, f.level * 0.35), p)


class Rainbow(Visualizer):
    id, name = "rainbow", "Dancing Rainbow"
    description = "Colors scroll faster the louder the music gets."
    preview = ("#ff2a2a", "#ffd21f", "#22e05a", "#1fe0ff", "#2f6bff", "#b53dff")

    def __init__(self):
        self.hue = 0.0

    def render(self, f, dt, p):
        self.hue += dt * (0.03 + 0.5 * f.level**2) + (0.08 if f.beat else 0.0)
        return _hsv(self.hue), self._lvl(0.25 + 0.75 * f.level, p)


class VuMeter(Visualizer):
    id, name = "vu", "VU Meter"
    description = "Blue when quiet, then green, yellow and red as it gets louder."
    preview = ("#2f6bff", "#22e05a", "#ffd21f", "#ff2a2a")

    def render(self, f, dt, p):
        return _hsv(0.66 * (1.0 - f.level)), self._lvl(0.2 + 0.8 * f.level, p)


class Fire(Visualizer):
    id, name = "fire", "Fire"
    description = "Red and orange flames that flare up with the music."
    preview = ("#3a0000", "#ff2a00", "#ff9a1f", "#ffd21f")

    def __init__(self):
        self.flicker = 0.0

    def render(self, f, dt, p):
        self.flicker += (random.uniform(-1, 1) - self.flicker) * min(1.0, dt * 12)
        heat = min(1.0, max(0.0, 0.6 * f.level + 0.4 * f.bass + 0.08 * self.flicker))
        return _hsv(0.005 + 0.11 * heat), self._lvl(0.3 + 0.7 * heat, p)


class Strobe(Visualizer):
    id, name = "strobe", "Strobe"
    description = "Short white flash on strong beats, dark the rest of the time. ⚠ Flashing light."
    uses_color = True
    preview = ("#000000", "#ffffff", "#000000", "#ffffff", "#000000")

    def __init__(self):
        self.on = 0.0

    def render(self, f, dt, p):
        if f.beat and f.beat_strength > 0.15:
            self.on = 0.06
        self.on -= dt
        return p.color, (1.0 if self.on > 0 else 0.0)


class AnimatedEffect(Visualizer):
    id, name = "animated", "Reactive Animation"
    description = "A controller effect (rainbow, comet…) keeps running while its brightness and speed follow the music."
    brightness_only = True
    preview = ("#ff2a2a", "#000000", "#22e05a", "#000000", "#2f6bff")

    def __init__(self):
        self.energy = 0.0

    def render(self, f, dt, p):
        # Slow-moving energy drives the effect speed (1..10), so the animation runs faster on loud parts.
        self.energy += (f.level - self.energy) * min(1.0, dt / 0.8)
        self.speed = 2 + round(8 * self.energy)
        return (0, 0, 0), self._lvl(0.15 + 0.85 * f.level, p)


class NativeEffects(Visualizer):
    id, name = "native", "Controller Effects"
    description = ("The controller's own music effects (Full Color Rhythm Spectrum, Rhythm Stars, VU Meter…) "
                   "animate each LED, driven by your PC audio instead of the controller's microphone.")
    native_feed = True
    preview = ("#ff2a2a", "#ffd21f", "#000000", "#22e05a", "#1fe0ff", "#000000", "#b53dff")

    def render(self, f, dt, p):
        return (0, 0, 0), self._lvl(f.level, p)  # preview only: the controller renders the effect


VISUALIZERS: list[type[Visualizer]] = [
    NativeEffects, Spectrum, Pulse, BeatFlash, Rainbow, Bass, RgbBands, VuMeter, Fire, AnimatedEffect, Strobe,
]


def create(vis_id: str) -> Visualizer:
    cls = next((v for v in VISUALIZERS if v.id == vis_id), Spectrum)
    return cls()
