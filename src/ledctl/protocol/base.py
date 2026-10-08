"""Types shared by every BanlanX protocol family."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from itertools import permutations
from typing import ClassVar, Literal

EffectKind = Literal["static", "dynamic", "sound"]

# Palette used to name effects and draw their previews.
COLORS: dict[str, tuple[str, str]] = {
    # key: (display name, preview color)
    "R": ("Red", "#ff2a2a"),
    "G": ("Green", "#22e05a"),
    "B": ("Blue", "#2f6bff"),
    "Y": ("Yellow", "#ffd21f"),
    "C": ("Cyan", "#1fe0ff"),
    "P": ("Purple", "#b53dff"),
    "W": ("White", "#f4f4f4"),
}
SEVEN = "RGBYCPW"
RAINBOW = ("#ff2a2a", "#ff9a1f", "#ffd21f", "#22e05a", "#1fe0ff", "#2f6bff", "#b53dff")


def color_names(keys: str, sep: str = " ") -> str:
    return sep.join(COLORS[k][0] for k in keys)


def color_hex(keys: str) -> tuple[str, ...]:
    return tuple(COLORS[k][1] for k in keys)


@dataclass(frozen=True)
class Effect:
    id: int
    name: str
    category: str
    kind: EffectKind
    colors: tuple[str, ...] = RAINBOW
    colorable: bool = False  # the effect uses the color picked by the user
    has_length: bool = False  # the "effect length" setting changes how the effect looks


@dataclass
class DeviceState:
    """State reported by the controller (every field is optional)."""

    power: bool | None = None
    light_mode: int | None = None  # 0 = single effect, 1 = cycle dynamic effects, 2 = cycle sound effects
    effect: int | None = None
    brightness: int | None = None
    speed: int | None = None
    length: int | None = None
    rgb: tuple[int, int, int] | None = None
    sensitivity: int | None = None
    audio_input: int | None = None
    chip_order: int | None = None
    white: int | None = None
    raw: bytes = b""

    def copy(self, **changes) -> DeviceState:
        return replace(self, **changes)


@dataclass(frozen=True)
class Model:
    """A controller model (SP611E, SP617E…)."""

    name: str
    description: str
    protocol: type[Protocol]
    colors: int = 3  # 3 = RGB, 4 = RGBW
    has_mic: bool = True
    manufacturer_ids: tuple[int, ...] = (20563,)
    manufacturer_prefixes: tuple[bytes, ...] = field(default=())

    def matches(self, manufacturer_data: dict[int, bytes]) -> bool:
        for mid, data in manufacturer_data.items():
            if mid in self.manufacturer_ids and any(data.startswith(p) for p in self.manufacturer_prefixes):
                return True
        return False


LIGHT_MODES = {0: "Single effect", 1: "Cycle effects", 2: "Cycle sound effects"}
AUDIO_INPUTS = {0: "Built-in microphone", 1: "Player (phone)", 2: "External microphone"}


def chip_orders(colors: int) -> list[str]:
    """Wiring orders, in the order the controller expects them (permutations of RGB)."""
    suffix = "W" if colors == 4 else ""
    return ["".join(p) + suffix for p in permutations("RGB")]


class Protocol(ABC):
    """Builds commands and decodes the state for one controller family."""

    family: ClassVar[str]
    write_uuid: ClassVar[str] = "0000ffe1-0000-1000-8000-00805f9b34fb"
    service_uuid: ClassVar[str] = "0000ffe0-0000-1000-8000-00805f9b34fb"

    max_speed: ClassVar[int] = 10
    max_length: ClassVar[int | None] = None
    max_sensitivity: ClassVar[int] = 16
    effect_solid: ClassVar[int]
    effect_white: ClassVar[int | None] = None
    supports_chip_order: ClassVar[bool] = False
    # Number of spectrum levels accepted by `cmd_audio_feed` (0 = the family cannot be fed audio data).
    feed_bands: ClassVar[int] = 0

    def __init__(self, model: Model):
        self.model = model
        self._pending = bytearray()

    # -- Catalogue ---------------------------------------------------------
    @abstractmethod
    def effects(self) -> list[Effect]: ...

    def dynamic_effects(self) -> list[Effect]:
        return [e for e in self.effects() if e.kind == "dynamic"]

    def sound_effects(self) -> list[Effect]:
        return [e for e in self.effects() if e.kind == "sound"] if self.model.has_mic else []

    def effect(self, effect_id: int | None) -> Effect | None:
        return next((e for e in self.effects() if e.id == effect_id), None)

    # -- Commands ----------------------------------------------------------
    @abstractmethod
    def cmd_query(self) -> bytes: ...
    @abstractmethod
    def cmd_power(self, on: bool) -> bytes: ...
    @abstractmethod
    def cmd_effect(self, effect_id: int) -> bytes: ...
    @abstractmethod
    def cmd_color(self, r: int, g: int, b: int, level: int) -> bytes: ...
    @abstractmethod
    def cmd_brightness(self, level: int) -> bytes: ...
    @abstractmethod
    def cmd_white(self, level: int) -> bytes: ...
    @abstractmethod
    def cmd_speed(self, speed: int) -> bytes: ...
    @abstractmethod
    def cmd_sensitivity(self, value: int) -> bytes: ...
    @abstractmethod
    def cmd_audio_input(self, value: int) -> bytes: ...
    @abstractmethod
    def cmd_light_mode(self, mode: int) -> bytes: ...

    def cmd_length(self, length: int) -> bytes | None:
        return None

    def cmd_chip_order(self, order: int) -> bytes | None:
        return None

    def cmd_audio_feed(self, levels: list[int]) -> bytes | None:
        """Audio levels (0..255) streamed to the built-in sound effects, like the app's phone-mic mode."""
        return None

    @property
    def supports_audio_feed(self) -> bool:
        return self.feed_bands > 0 and bool(self.sound_effects())

    # -- Notifications -----------------------------------------------------
    @abstractmethod
    def feed(self, data: bytes) -> DeviceState | None:
        """Feeds one received notification; returns the state once a full message has arrived."""


def clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(value)))
