"""Screen ambiance: the strip takes the dominant color of the screen (one color for the whole strip).

Capture uses DXGI Desktop Duplication on Windows (dxcam: the image is read from the GPU, and nothing is
copied while the screen does not change) and mss elsewhere, or when DXGI is not available.
"""

from __future__ import annotations

import colorsys
import logging
import math
import re
import sys
import threading
import time
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

SAMPLE_WIDTH = 96  # the captured image is subsampled to about this many columns


@dataclass(frozen=True)
class Monitor:
    index: int  # backend-specific output index
    label: str


@dataclass
class ScreenParams:
    area: str = "full"  # "full" or "edges"
    boost: float = 1.3  # saturation multiplier
    smoothing: float = 0.3  # seconds
    floor: float = 0.05  # minimum brightness (0..1)


def _use_dxgi() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import dxcam  # noqa: F401
    except ImportError:
        return False
    return True


def list_monitors() -> list[Monitor]:
    if _use_dxgi():
        import dxcam

        monitors = []
        for line in dxcam.output_info().splitlines():
            m = re.search(r"Device\[(\d+)\] Output\[(\d+)\]: Res:\((\d+), (\d+)\).*Primary:(\w+)", line)
            if m and m.group(1) == "0":
                primary = " (primary)" if m.group(5) == "True" else ""
                monitors.append(Monitor(int(m.group(2)), f"Screen {len(monitors) + 1} — {m.group(3)}×{m.group(4)}{primary}"))
        if monitors:
            return monitors

    with _mss() as sct:
        return [Monitor(i, f"Screen {i} — {m['width']}×{m['height']}") for i, m in enumerate(sct.monitors[1:], start=1)]


def _mss():
    import mss

    return (getattr(mss, "MSS", None) or mss.mss)()


class _DxgiGrabber:
    def __init__(self, output: int):
        import dxcam

        self._cam = dxcam.create(output_idx=output, output_color="BGRA")
        self._last: np.ndarray | None = None

    def grab(self) -> np.ndarray | None:
        frame = self._cam.grab()  # None when the screen did not change
        if frame is not None:
            step = max(1, frame.shape[1] // SAMPLE_WIDTH)
            self._last = frame[::step, ::step, 2::-1].astype(np.float32) / 255  # BGRA → RGB
        return self._last

    def close(self) -> None:
        del self._cam


class _MssGrabber:
    def __init__(self, output: int):
        self._sct = _mss()
        monitors = self._sct.monitors
        self._monitor = monitors[output if 0 < output < len(monitors) else 1]

    def grab(self) -> np.ndarray:
        shot = self._sct.grab(self._monitor)
        frame = np.frombuffer(shot.bgra, np.uint8).reshape(shot.height, shot.width, 4)
        step = max(1, shot.width // SAMPLE_WIDTH)
        return frame[::step, ::step, 2::-1].astype(np.float32) / 255

    def close(self) -> None:
        self._sct.close()


def _open_grabber(output: int):
    if _use_dxgi():
        try:
            return _DxgiGrabber(output)
        except Exception:  # noqa: BLE001 - e.g. no DXGI output (remote desktop): fall back to GDI
            log.exception("DXGI capture unavailable, falling back to mss")
    return _MssGrabber(output if not _use_dxgi() else output + 1)


def dominant_color(img: np.ndarray, area: str = "full", boost: float = 1.3) -> tuple[tuple[int, int, int], float]:
    """Returns the dominant color (full intensity) and the overall brightness (0..1) of an RGB image.

    A plain average turns colorful images into a dull grey/brown: bright, saturated pixels weigh more,
    near-black pixels (letterbox bars, dark UI) are ignored.
    """
    h, w = img.shape[:2]
    if area == "edges":
        bh, bw = max(1, h // 6), max(1, w // 6)
        mask = np.zeros((h, w), bool)
        mask[:bh], mask[-bh:], mask[:, :bw], mask[:, -bw:] = True, True, True, True
        px = img[mask]
    else:
        px = img.reshape(-1, 3)
    value = px.max(axis=1)
    sat = (value - px.min(axis=1)) / (value + 1e-6)
    # Intensity rather than perceived luminance: a fully red or blue screen must light the strip fully.
    brightness = float(min(1.0, value.mean() * 1.2))

    valid = value > 0.1
    if valid.sum() < max(1, len(px) // 100):
        return (255, 255, 255), 0.0  # dark screen
    weight = (sat[valid] ** 2 + 0.05) * value[valid]
    color = (px[valid] * weight[:, None]).sum(axis=0) / weight.sum()
    hue, s, _ = colorsys.rgb_to_hsv(*color)
    r, g, b = colorsys.hsv_to_rgb(hue, min(1.0, s * boost), 1.0)
    return (round(r * 255), round(g * 255), round(b * 255)), brightness


class ScreenEngine(QObject):
    frame = Signal(object, object, float)  # thumbnail (RGB uint8 array), rgb, brightness 0..1 (preview)
    running_changed = Signal(bool)
    error = Signal(str)

    def __init__(self, send) -> None:
        """`send(cmd, key)`: coalescing send function to the controller."""
        super().__init__()
        self._send = send
        self.protocol = None
        self.monitor = 0
        self.params = ScreenParams()
        self.fps = 15
        self.master_brightness = 255
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="screen-engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if not self.running:
            return
        self._stop.set()
        self._thread.join(timeout=2)
        self._thread = None

    def _run(self) -> None:
        try:
            grabber = _open_grabber(self.monitor)
        except Exception as exc:  # noqa: BLE001
            log.exception("Screen capture unavailable")
            self.error.emit(f"Screen capture is not available: {exc}")
            return
        self.running_changed.emit(True)
        rgb = np.array([255.0, 255.0, 255.0])
        level = 0.0
        last_cmd, last_sent, last_preview = b"", 0.0, 0.0
        prev = time.perf_counter()
        next_tick = prev
        try:
            while not self._stop.is_set():
                now = time.perf_counter()
                dt, prev = now - prev, now
                img = grabber.grab()
                if img is not None:
                    p = self.params
                    target, target_level = dominant_color(img, p.area, p.boost)
                    k = 1.0 - math.exp(-dt / max(p.smoothing, 1e-3))
                    rgb += (np.array(target, float) - rgb) * k
                    level += (target_level - level) * k
                    out_rgb = tuple(int(round(c)) for c in rgb)
                    out_level = p.floor + (1 - p.floor) * level
                    proto = self.protocol
                    if proto is not None:
                        cmd = proto.cmd_color(*out_rgb, round(out_level * self.master_brightness))
                        if cmd != last_cmd or now - last_sent > 0.5:
                            self._send(cmd, "screen")
                            last_cmd, last_sent = cmd, now
                    if now - last_preview > 1 / 15:
                        thumb = (np.clip(img, 0, 1) * 255).astype(np.uint8)
                        self.frame.emit(thumb, out_rgb, out_level)
                        last_preview = now
                next_tick += 1.0 / max(1, self.fps)
                delay = next_tick - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
                else:
                    next_tick = time.perf_counter()
        except Exception as exc:  # noqa: BLE001
            log.exception("Screen engine error")
            self.error.emit(f"Screen capture error: {exc}")
        finally:
            try:
                grabber.close()
            except Exception:  # noqa: BLE001
                pass
            self.running_changed.emit(False)
