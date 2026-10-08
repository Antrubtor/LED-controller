"""BanlanX v3 protocol: SP613E, SP614E, SP623E, SP624E (PWM RGB/RGBW controllers).

Format: <opcode> <length> <payload>.
"""

from __future__ import annotations

from .base import RAINBOW, SEVEN, DeviceState, Effect, Protocol, clamp, color_hex, color_names

OP_POWER = 0x0F
OP_BRIGHTNESS = 0x12
OP_COLOR = 0x13
OP_SPEED = 0x14
OP_EFFECT = 0x15
OP_LIGHT_MODE = 0x16
OP_SENSITIVITY = 0x17
OP_AUDIO_INPUT = 0x19
OP_QUERY = 0x1D
OP_WHITE = 0x21

EFFECT_SOLID = 0x63
EFFECT_CUSTOM = 0x64
EFFECT_WHITE = 0xCC


def _build_effects() -> list[Effect]:
    fx = [
        Effect(0x01, "Seven Color Gradient", "Rainbow", "dynamic"),
        Effect(0x02, "Seven Color Jump", "Rainbow", "dynamic"),
        Effect(0x03, "Seven Color Breathe", "Breath", "dynamic"),
        Effect(0x04, "Seven Color Strobe", "Strobe", "dynamic"),
    ]
    for n, c in enumerate(SEVEN):
        fx.append(Effect(0x05 + n, f"{color_names(c)} Breath", "Breath", "dynamic", ("#000000", *color_hex(c), "#000000")))
    for n, c in enumerate(SEVEN):
        fx.append(Effect(0x0C + n, f"{color_names(c)} Strobe", "Strobe", "dynamic", ("#000000", *color_hex(c))))
    fx.append(Effect(0x20, "Custom Color Breath", "Breath", "dynamic", ("#000000", "#7c5cff"), colorable=True))
    fx.append(Effect(0x21, "Custom Color Strobe", "Strobe", "dynamic", ("#000000", "#7c5cff"), colorable=True))
    fx.append(Effect(EFFECT_CUSTOM, "Custom (app)", "Rainbow", "dynamic"))
    return fx


DYNAMIC_EFFECTS = _build_effects()
SOUND_EFFECTS = [
    Effect(0x65, "Music Breathe", "Music", "sound", RAINBOW),
    Effect(0x66, "Music Jump", "Music", "sound", RAINBOW),
    Effect(0x67, "Monochrome Music Breathe", "Music", "sound", ("#000000", "#7c5cff"), colorable=True),
]
SOLID = Effect(EFFECT_SOLID, "Solid Color", "Static", "static", colorable=True)
WHITE = Effect(EFFECT_WHITE, "White", "Static", "static", ("#f4f4f4",))


class BanlanX3(Protocol):
    family = "BanlanX v3"
    max_length = None
    max_sensitivity = 15
    effect_solid = EFFECT_SOLID
    _total = 0

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
        return bytes([op, len(data), *(d & 0xFF for d in data)])

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
        return self._cmd(OP_WHITE, clamp(level, 0, 255), 0xFF)

    def cmd_speed(self, speed: int) -> bytes:
        return self._cmd(OP_SPEED, clamp(speed, 1, self.max_speed))

    def cmd_sensitivity(self, value: int) -> bytes:
        return self._cmd(OP_SENSITIVITY, clamp(value, 1, self.max_sensitivity))

    def cmd_audio_input(self, value: int) -> bytes:
        return self._cmd(OP_AUDIO_INPUT, clamp(value, 0, 2))

    def cmd_light_mode(self, mode: int) -> bytes:
        return self._cmd(OP_LIGHT_MODE, clamp(mode, 0, 2))

    # Split messages: <packet #> <total length> <packet length> <data> (next packets: <#> <length> <data>)
    def feed(self, data: bytes) -> DeviceState | None:
        if len(data) < 3:
            return None
        packet = data[0]
        if packet == 1:
            self._total = data[1]
            self._pending = bytearray(data[3:])
        elif self._pending:
            self._pending += data[2:]
        else:
            return None
        if len(self._pending) < self._total:
            return None
        message, self._pending = bytes(self._pending[: self._total]), bytearray()
        return self.parse_status(message)

    def parse_status(self, d: bytes) -> DeviceState | None:
        if len(d) < 10:
            return None
        n = len(d)
        return DeviceState(
            power=d[0] == 1,
            brightness=d[1],
            speed=d[2],
            chip_order=d[3],
            effect=d[4],
            light_mode=d[5],
            rgb=(d[6], d[7], d[8]),
            sensitivity=d[9],
            audio_input=d[n - 3] if self.model.has_mic and n >= 13 else None,
            white=d[n - 2] if self.model.colors == 4 and n >= 13 else None,
            raw=d,
        )
