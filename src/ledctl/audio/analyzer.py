"""Signal analysis: per-band levels, automatic gain, envelopes and beat detection."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

import numpy as np

SILENCE_DB = -65.0
DYN_RANGE_DB = 12.0  # minimum gap between the adaptive floor and peak


def sensitivity_threshold(gain: float) -> float:
    """Gate applied after normalization: low sensitivity = only the loud hits light the strip.

    `gain` is the stored sensitivity (×0.3 … ×3, i.e. levels 1 … 16 in the UI): level 1 keeps only the
    top 15 % of the dynamics, level 16 lets everything through.
    """
    level = 1 + 15 * math.log10(min(max(gain, 0.3), 3.0) / 0.3)
    return 0.85 * (16 - level) / 15


def _gate(x, t: float):
    return np.maximum(0.0, (x - t) / (1.0 - t)) if isinstance(x, np.ndarray) else max(0.0, (x - t) / (1.0 - t))


@dataclass
class AudioFrame:
    level: float = 0.0  # 0..1, smoothed overall energy
    bass: float = 0.0
    mid: float = 0.0
    treble: float = 0.0
    centroid: float = 0.0  # 0 (low) .. 1 (high)
    beat: bool = False
    beat_strength: float = 0.0
    bands: np.ndarray = field(default_factory=lambda: np.zeros(0))


class _Agc:
    """Normalizes a dB value between a floor and a peak that adapt to the track.

    The peak follows loud passages (and decays slowly), the floor follows the dips between hits
    (and rises slowly). Result: 0 in the dips, 1 on the hits, whatever the volume.
    """

    def __init__(self) -> None:
        self.peak = -30.0
        self.floor = -60.0

    def __call__(self, db: float, dt: float, min_range: float) -> float:
        self.peak = max(db, self.peak - 3.0 * dt)
        self.floor = min(db, self.floor + 5.0 * dt)
        span = max(self.peak - self.floor, min_range)
        return min(1.0, max(0.0, (db - (self.peak - span)) / span))


class _Envelope:
    """Near-instant attack, adjustable release: smooth without losing the hits."""

    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self, x: float, dt: float, attack: float, release: float) -> float:
        tau = attack if x > self.value else release
        self.value += (x - self.value) * (1.0 - math.exp(-dt / max(tau, 1e-3)))
        return self.value


class Analyzer:
    BASS = (30, 150)
    MID = (150, 2000)
    TREBLE = (2000, 12000)

    def __init__(self, rate: int, n_fft: int = 2048, n_bands: int = 24) -> None:
        self.rate = rate
        self.n_fft = n_fft
        self.sensitivity = 1.0  # 0.3 .. 3
        self.smoothing = 0.15  # release time (s)
        self._window = np.hanning(n_fft).astype(np.float32)
        freqs = np.fft.rfftfreq(n_fft, 1.0 / rate)
        self._masks = {
            name: (freqs >= lo) & (freqs < hi)
            for name, (lo, hi) in (("bass", self.BASS), ("mid", self.MID), ("treble", self.TREBLE))
        }
        edges = np.geomspace(40, min(16000, rate / 2 - 1), n_bands + 1)
        self._band_idx = [np.where((freqs >= lo) & (freqs < hi))[0] for lo, hi in zip(edges[:-1], edges[1:])]
        self._band_idx = [idx if len(idx) else np.array([np.argmin(abs(freqs - lo))]) for idx, lo in zip(self._band_idx, edges)]
        self._freqs = freqs
        self._agc = {k: _Agc() for k in ("level", "bass", "mid", "treble")}
        self._band_peak = np.full(n_bands, -30.0)
        self._band_floor = np.full(n_bands, -60.0)
        self._env = {k: _Envelope() for k in ("level", "bass", "mid", "treble", "centroid")}
        self._bands_env = np.zeros(n_bands)
        self._bass_hist: deque[float] = deque(maxlen=43)  # ~0.7 s at 60 fps
        self._since_beat = 1.0

    def process(self, samples: np.ndarray, dt: float) -> AudioFrame:
        dt = max(dt, 1e-3)
        dyn_range = DYN_RANGE_DB
        gate = sensitivity_threshold(self.sensitivity)
        rms_db = 10 * math.log10(float(np.mean(samples * samples)) + 1e-12)
        silent = rms_db < SILENCE_DB

        spec = np.abs(np.fft.rfft(samples * self._window)) ** 2 / self.n_fft
        raw = {"level": 0.0}
        lin = {}
        for name, mask in self._masks.items():
            lin[name] = float(spec[mask].sum())
            db = 10 * math.log10(lin[name] + 1e-12)
            raw[name] = 0.0 if silent else self._agc[name](db, dt, dyn_range)
        raw["level"] = 0.0 if silent else self._agc["level"](rms_db, dt, dyn_range)
        normalized_bass = raw["bass"]

        total = float(spec.sum()) + 1e-12
        centroid_hz = float((spec * self._freqs).sum() / total)
        centroid = min(1.0, max(0.0, math.log(max(centroid_hz, 60) / 60) / math.log(8000 / 60)))

        release = max(0.03, self.smoothing)
        frame = AudioFrame()
        # The sensitivity gate comes after the envelopes, so the smoothed release cannot fill the gaps.
        frame.level = _gate(self._env["level"](raw["level"], dt, 0.012, release), gate)
        frame.bass = _gate(self._env["bass"](raw["bass"], dt, 0.008, release), gate)
        frame.mid = _gate(self._env["mid"](raw["mid"], dt, 0.012, release), gate)
        frame.treble = _gate(self._env["treble"](raw["treble"], dt, 0.008, release * 0.7), gate)
        frame.centroid = self._env["centroid"](0.0 if silent else centroid, dt, 0.08, 0.25)

        band_db = np.array([10 * np.log10(spec[idx].sum() + 1e-12) for idx in self._band_idx])
        self._band_peak = np.maximum(band_db, self._band_peak - 3.0 * dt)
        self._band_floor = np.minimum(band_db, self._band_floor + 5.0 * dt)
        span = np.maximum(self._band_peak - self._band_floor, dyn_range * 1.5)
        bands = np.clip((band_db - (self._band_peak - span)) / span, 0, 1)
        if silent:
            bands[:] = 0
        k = 1.0 - math.exp(-dt / release)
        self._bands_env = np.where(bands > self._bands_env, bands, self._bands_env + (bands - self._bands_env) * k)
        frame.bands = _gate(self._bands_env, gate)

        # Beat: the bass energy clearly exceeds its recent average.
        self._since_beat += dt
        bass = lin["bass"]
        if self._bass_hist and not silent:
            hist = np.array(self._bass_hist)
            mean, std = hist.mean(), hist.std()
            threshold = mean + max(1.3 * std, 0.45 * mean)
            if bass > threshold and normalized_bass > max(0.35, gate) and self._since_beat > 0.18:
                frame.beat = True
                frame.beat_strength = float(min(1.0, (bass - mean) / (3 * mean + 1e-12)))
                self._since_beat = 0.0
        self._bass_hist.append(bass)
        return frame
