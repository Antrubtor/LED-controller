"""BanlanX v2 protocol: SP611E, SP617E, SP620E, SP621E.

Every command starts with 0xA0, followed by the opcode, the payload length and the payload.
Sources: the UniLED project (monty68/uniled) and tests on a real SP611E.
"""

from __future__ import annotations

from itertools import combinations

from .base import (
    RAINBOW,
    SEVEN,
    DeviceState,
    Effect,
    Protocol,
    clamp,
    color_hex,
    color_names,
)

OP_POWER = 0x62
OP_EFFECT = 0x63
OP_CHIP_ORDER = 0x64
OP_BRIGHTNESS = 0x66
OP_SPEED = 0x67
OP_LENGTH = 0x68
OP_COLOR = 0x69
OP_LIGHT_MODE = 0x6A
OP_SENSITIVITY = 0x6B
OP_AUDIO_INPUT = 0x6C
OP_AUDIO_FEED = 0x6D  # found by testing on an SP611E; payload longer than 17 bytes hangs the controller
OP_QUERY = 0x70
OP_WHITE = 0x76

EFFECT_SOLID = 0xBE
EFFECT_WHITE = 0xBF


def _build_effects() -> list[Effect]:
    """The 142 dynamic effects, named like in the BanlanX app."""
    fx: list[Effect] = []
    i = 0x01

    def add(name: str, cat: str, colors: tuple[str, ...] = RAINBOW) -> None:
        nonlocal i
        fx.append(Effect(i, name, cat, "dynamic", colors))
        i += 1

    add("Rainbow", "Rainbow")
    add("Rainbow Meteor", "Rainbow")
    add("Rainbow Stars", "Rainbow")
    add("Rainbow Spin", "Rainbow")
    for pair in ("RY", "RP", "GY", "GC", "BP", "BC"):
        add(f"{color_names(pair)} Fire", "Fire", color_hex(pair))
    for c in SEVEN:
        add(f"{color_names(c)} Comet", "Comet", ("#000000", *color_hex(c)))
    for c in SEVEN:
        add(f"{color_names(c)} Meteor", "Meteor", ("#000000", *color_hex(c), "#000000"))
    for a, b in combinations(SEVEN, 2):
        add(f"{color_names(a + b)} Gradual Snake", "Snake", color_hex(a + b))
    for c in SEVEN:
        add(f"{color_names(c)} Wave", "Wave", ("#000000", *color_hex(c), "#000000"))
    for a, b in combinations(SEVEN, 2):
        add(f"{color_names(a + b)} Wave", "Wave", color_hex(a + b))
    for c in SEVEN:
        add(f"{color_names(c)} Stars", "Stars", ("#000000", *color_hex(c), "#000000", *color_hex(c)))
    for c in SEVEN[:6]:
        add(f"{color_names(c)} Background Stars", "Stars", color_hex(c * 2))
    for c in SEVEN:
        add(f"{color_names(c)} White Background Stars", "Stars", color_hex(c + "W" + c))
    for c in SEVEN:
        add(f"{color_names(c)} Breath", "Breath", ("#000000", *color_hex(c), "#000000"))
    for c in SEVEN:
        add(f"{color_names(c)} Stacking", "Stacking", ("#000000", *color_hex(c)))
    add("Full Color Stack", "Stacking")
    for pair in ("RG", "GB", "BY", "YC", "CP", "PW"):
        add(f"{color_names(pair, ' to ')} Stack", "Stacking", color_hex(pair))
    for combo in ("RBW", "GYW", "RGW", "RY", "RW", "GW"):
        add(f"{color_names(combo)} Snake", "Snake", color_hex(combo))
    for label in ("Comet Spin", "Dot Spin", "Segment Spin"):
        for c in SEVEN:
            add(f"{color_names(c)} {label}", "Spin", ("#000000", *color_hex(c), "#000000"))
    add("Gradient", "Rainbow")
    assert i == 0x8F, hex(i)
    return fx


_SOUND = [
    (0xC9, "Full Color Rhythm Spectrum", False),
    (0xCA, "Single Color Rhythm Spectrum", True),
    (0xCB, "Full Color Rhythm Stars", False),
    (0xCC, "Single Color Rhythm Stars", True),
    (0xCD, "Gradient Energy", False),
    (0xCE, "Single Color Energy", True),
    (0xCF, "Gradient Pulse", False),
    (0xD0, "Single Color Pulse", True),
    (0xD1, "Full Color Ejection Forward", False),
    (0xD2, "Single Color Ejection Forward", True),
    (0xD3, "Full Color Ejection Backward", False),
    (0xD4, "Single Color Ejection Backward", True),
    (0xD5, "Full Color VuMeter", False),
    (0xD6, "Single Color VuMeter", True),
    (0xD7, "Love & Peace", False),
    (0xD8, "Christmas", False),
    (0xD9, "Heartbeat", False),
    (0xDA, "Party", False),
]

_SOUND_PALETTES = {
    0xD7: ("#ff2a6a", "#f4f4f4", "#2f6bff"),
    0xD8: ("#ff2a2a", "#22e05a", "#f4f4f4"),
    0xD9: ("#000000", "#ff2a4a", "#000000", "#ff2a4a"),
}

# Sound effects whose "effect length" is adjustable in the BanlanX app (Energy, Ejection, VuMeter).
_SOUND_WITH_LENGTH = {0xCD, 0xCE, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6}

DYNAMIC_EFFECTS = [Effect(e.id, e.name, e.category, e.kind, e.colors, has_length=True) for e in _build_effects()]
SOUND_EFFECTS = [
    Effect(eid, name, "Music", "sound",
           ("#000000", "#7c5cff") if single else _SOUND_PALETTES.get(eid, RAINBOW), colorable=single,
           has_length=eid in _SOUND_WITH_LENGTH)
    for eid, name, single in _SOUND
]
SOLID = Effect(EFFECT_SOLID, "Solid Color", "Static", "static", colorable=True)
WHITE = Effect(EFFECT_WHITE, "White", "Static", "static", ("#f4f4f4",))


class BanlanX2(Protocol):
    family = "BanlanX v2"
    max_length = 150
    effect_solid = EFFECT_SOLID
    supports_chip_order = True
    feed_bands = 16

    @property
    def effect_white(self) -> int | None:  # type: ignore[override]
        return EFFECT_WHITE if self.model.colors == 4 else None

    def effects(self) -> list[Effect]:
        fx = [SOLID]
        if self.model.colors == 4:
            fx.append(WHITE)
        fx += DYNAMIC_EFFECTS
        if self.model.has_mic:
            fx += SOUND_EFFECTS
        return fx

    @staticmethod
    def _cmd(op: int, *data: int) -> bytes:
        return bytes([0xA0, op, len(data), *(d & 0xFF for d in data)])

    def cmd_query(self) -> bytes:
        return self._cmd(OP_QUERY)

    def cmd_power(self, on: bool) -> bytes:
        return self._cmd(OP_POWER, 1 if on else 0)

    def cmd_effect(self, effect_id: int) -> bytes:
        return self._cmd(OP_EFFECT, effect_id)

    def cmd_color(self, r: int, g: int, b: int, level: int) -> bytes:
        return self._cmd(OP_COLOR, r, g, b, clamp(level, 0, 255))

    def cmd_brightness(self, level: int) -> bytes:
        return self._cmd(OP_BRIGHTNESS, clamp(level, 0, 255))

    def cmd_white(self, level: int) -> bytes:
        return self._cmd(OP_WHITE, clamp(level, 0, 255), 0)

    def cmd_speed(self, speed: int) -> bytes:
        return self._cmd(OP_SPEED, clamp(speed, 1, self.max_speed))

    def cmd_length(self, length: int) -> bytes:
        return self._cmd(OP_LENGTH, clamp(length, 1, self.max_length))

    def cmd_sensitivity(self, value: int) -> bytes:
        return self._cmd(OP_SENSITIVITY, clamp(value, 1, self.max_sensitivity))

    def cmd_audio_input(self, value: int) -> bytes:
        return self._cmd(OP_AUDIO_INPUT, clamp(value, 0, 2))

    def cmd_light_mode(self, mode: int) -> bytes:
        return self._cmd(OP_LIGHT_MODE, clamp(mode, 0, 2))

    def cmd_chip_order(self, order: int) -> bytes:
        return self._cmd(OP_CHIP_ORDER, clamp(order, 0, 5))

    def cmd_audio_feed(self, levels: list[int]) -> bytes:
        # Only works when the audio input is set to "Player" (1); the controller then waits for this data.
        return self._cmd(OP_AUDIO_FEED, *(clamp(v, 0, 255) for v in levels[: self.feed_bands]))

    # Status messages arrive split: "SC" <packet #> <total length> <packet length> <data>
    def feed(self, data: bytes) -> DeviceState | None:
        if len(data) > 5 and data[0] == 0x53 and data[1] == 0x43:
            packet, total = data[2], data[3]
            if packet == 1:
                self._pending = bytearray(data[5:])
            elif self._pending:
                self._pending += data[5:]
            else:
                return None
            if len(self._pending) < total:
                return None
            message, self._pending = bytes(self._pending[:total]), bytearray()
        elif len(data) >= 12 and data[0] in (0, 1):
            message = bytes(data)  # some firmwares send the state without a header
        else:
            return None
        return self.parse_status(message)

    def parse_status(self, d: bytes) -> DeviceState | None:
        if len(d) < 12:
            return None
        return DeviceState(
            power=d[0] == 1,
            light_mode=d[1],
            effect=d[2],
            chip_order=d[3],
            brightness=d[4],
            speed=d[5],
            length=d[6],
            rgb=(d[7], d[8], d[9]),
            audio_input=d[10],
            sensitivity=d[11],
            white=d[-2] if self.model.colors == 4 and len(d) >= 25 else None,
            raw=d,
        )
