"""Non-blocking audio capture (WASAPI): PC sound (loopback) or a microphone.

PortAudio calls a callback that fills a ring buffer, and the analysis always reads the most
recent samples. A blocking read followed by a `sleep` lets the sound card buffer fill faster
than it is drained, which makes the latency grow without bound.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

import numpy as np

try:
    import pyaudiowpatch as pyaudio
except ImportError:  # not on Windows
    pyaudio = None

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AudioDevice:
    index: int
    name: str
    kind: str  # "loopback" (PC sound) or "input" (microphone)
    rate: int
    channels: int

    @property
    def label(self) -> str:
        prefix = "🔊 PC sound" if self.kind == "loopback" else "🎤 Microphone"
        return f"{prefix} — {self.name.replace(' [Loopback]', '')}"


def available() -> bool:
    return pyaudio is not None


def list_devices() -> list[AudioDevice]:
    if pyaudio is None:
        return []
    p = pyaudio.PyAudio()
    try:
        wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
        out: list[AudioDevice] = []
        for i in range(p.get_device_count()):
            d = p.get_device_info_by_index(i)
            if d["hostApi"] != wasapi["index"] or d["maxInputChannels"] < 1:
                continue
            kind = "loopback" if d.get("isLoopbackDevice") else "input"
            out.append(AudioDevice(d["index"], d["name"], kind, int(d["defaultSampleRate"]), int(d["maxInputChannels"])))
        out.sort(key=lambda dev: dev.kind != "loopback")
        return out
    finally:
        p.terminate()


def default_loopback() -> AudioDevice | None:
    """The loopback device of the default Windows audio output."""
    if pyaudio is None:
        return None
    p = pyaudio.PyAudio()
    try:
        speakers = p.get_default_wasapi_loopback()
        return AudioDevice(speakers["index"], speakers["name"], "loopback",
                           int(speakers["defaultSampleRate"]), int(speakers["maxInputChannels"]))
    except Exception:  # noqa: BLE001
        log.exception("No default loopback device")
        return None
    finally:
        p.terminate()


class AudioCapture:
    def __init__(self, device: AudioDevice, seconds: float = 1.0) -> None:
        self.device = device
        self.rate = device.rate
        self._buf = np.zeros(int(self.rate * seconds), dtype=np.float32)
        self._pos = 0
        self._lock = threading.Lock()
        self._last_data = 0.0
        self._pa = None
        self._stream = None

    def start(self) -> None:
        if pyaudio is None:
            raise RuntimeError("Audio capture unavailable (pyaudiowpatch is only installed on Windows).")
        self._pa = pyaudio.PyAudio()
        try:
            self._stream = self._pa.open(
                format=pyaudio.paFloat32,
                channels=self.device.channels,
                rate=self.rate,
                input=True,
                input_device_index=self.device.index,
                frames_per_buffer=256,
                stream_callback=self._callback,
            )
        except Exception:
            self._pa.terminate()
            self._pa = None
            raise

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:  # noqa: BLE001
                pass
            self._stream = None
        if self._pa is not None:
            self._pa.terminate()
            self._pa = None

    def _callback(self, in_data, frame_count, _time_info, _status):
        data = np.frombuffer(in_data, dtype=np.float32)
        if self.device.channels > 1:
            data = data.reshape(-1, self.device.channels).mean(axis=1)
        n = len(data)
        size = len(self._buf)
        with self._lock:
            end = self._pos + n
            if end <= size:
                self._buf[self._pos:end] = data
            else:
                first = size - self._pos
                self._buf[self._pos:] = data[:first]
                self._buf[: n - first] = data[first:]
            self._pos = end % size
            self._last_data = time.perf_counter()
        return None, pyaudio.paContinue

    def latest(self, n: int) -> np.ndarray:
        """The last `n` samples (mono). Silence if nothing arrives (WASAPI sends nothing when nothing plays)."""
        if time.perf_counter() - self._last_data > 0.2:
            return np.zeros(n, dtype=np.float32)
        with self._lock:
            idx = (np.arange(self._pos - n, self._pos)) % len(self._buf)
            return self._buf[idx].copy()
