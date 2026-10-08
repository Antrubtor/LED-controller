"""PC music mode loop: capture → analysis → visualizer → controller, at a fixed rate."""

from __future__ import annotations

import logging
import threading
import time

import numpy as np

from PySide6.QtCore import QObject, Signal

from .analyzer import Analyzer
from .capture import AudioCapture, AudioDevice, default_loopback
from .visualizers import VisualParams, Visualizer, create

log = logging.getLogger(__name__)


class MusicEngine(QObject):
    frame = Signal(object, object, float)  # AudioFrame, rgb, brightness 0..1 (preview)
    running_changed = Signal(bool)
    error = Signal(str)

    def __init__(self, send) -> None:
        """`send(cmd, key)`: coalescing send function to the controller."""
        super().__init__()
        self._send = send
        self.protocol = None
        self.device: AudioDevice | None = None
        self.visualizer: Visualizer = create("spectrum")
        self.params = VisualParams()
        self.fps = 20
        self.sensitivity = 1.0
        self.smoothing = 0.15
        self.master_brightness = 255
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def set_visualizer(self, vis_id: str) -> None:
        if vis_id != self.visualizer.id:
            self.visualizer = create(vis_id)

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="music-engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if not self.running:
            return
        self._stop.set()
        self._thread.join(timeout=2)
        self._thread = None

    @staticmethod
    def _feed_levels(bands: np.ndarray, count: int) -> list[int]:
        """Resamples the analyzer bands (low → high frequencies) to the number of levels the controller takes."""
        if count <= 0 or len(bands) == 0:
            return []
        x = np.linspace(0, len(bands) - 1, count)
        return [round(255 * v) for v in np.clip(np.interp(x, np.arange(len(bands)), bands), 0, 1)]

    def _run(self) -> None:
        device = self.device or default_loopback()
        if device is None:
            self.error.emit("No audio source found.")
            return
        capture = AudioCapture(device)
        try:
            capture.start()
        except Exception as exc:  # noqa: BLE001
            log.exception("Could not open audio")
            message = f"Could not open \"{device.name}\": {exc}"
            if device.kind == "input":
                message += ("\n\nIf every microphone fails, Windows is probably blocking microphone access for "
                            "desktop apps: Settings → Privacy & security → Microphone → turn on "
                            "\"Let desktop apps access your microphone\".")
            self.error.emit(message)
            return

        self.running_changed.emit(True)
        analyzer = Analyzer(capture.rate)
        last_cmd = b""
        last_sent = 0.0
        last_preview = 0.0
        last_speed, speed_sent = None, 0.0
        prev = time.perf_counter()
        next_tick = prev
        try:
            while not self._stop.is_set():
                now = time.perf_counter()
                dt, prev = now - prev, now
                # Controller effects get the full dynamics: there, the sensitivity is the controller's own (1–16).
                analyzer.sensitivity = 3.0 if self.visualizer.native_feed else self.sensitivity
                analyzer.smoothing = self.smoothing
                frame = analyzer.process(capture.latest(analyzer.n_fft), dt)
                vis = self.visualizer
                rgb, level = vis.render(frame, dt, self.params)
                level_byte = round(level * self.master_brightness)

                proto = self.protocol
                if proto is not None:
                    if vis.native_feed:
                        cmd = proto.cmd_audio_feed(self._feed_levels(frame.bands, proto.feed_bands))
                    elif vis.brightness_only:
                        cmd = proto.cmd_brightness(level_byte)
                    else:
                        cmd = proto.cmd_color(*rgb, level_byte)
                    # No need to resend an identical value, except now and then (lost packet).
                    if cmd != last_cmd or now - last_sent > 0.5:
                        self._send(cmd, "music")
                        last_cmd, last_sent = cmd, now
                    if vis.speed is not None and vis.speed != last_speed and now - speed_sent > 0.25:
                        self._send(proto.cmd_speed(vis.speed), "music_speed")
                        last_speed, speed_sent = vis.speed, now

                if now - last_preview > 1 / 30:
                    self.frame.emit(frame, rgb, level)
                    last_preview = now

                next_tick += 1.0 / max(5, self.fps)
                delay = next_tick - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
                else:
                    next_tick = time.perf_counter()  # we are late: do not try to catch up
        except Exception as exc:  # noqa: BLE001
            log.exception("Music engine error")
            self.error.emit(f"Audio error: {exc}")
        finally:
            capture.stop()
            self.running_changed.emit(False)
