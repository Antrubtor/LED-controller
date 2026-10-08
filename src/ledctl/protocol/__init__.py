"""Supported BanlanX controllers and automatic model detection."""

from __future__ import annotations

from .banlanx2 import BanlanX2
from .banlanx3 import BanlanX3
from .base import AUDIO_INPUTS, LIGHT_MODES, DeviceState, Effect, Model, Protocol, chip_orders

BANLANX_IDS = (20563, 5053)  # 20563 = 0x5053 = "SP"

MODELS: list[Model] = [
    Model("SP611E", "SPI RGB music controller", BanlanX2, 3, True, BANLANX_IDS,
          # 0x8E: seen on a real SP611E (not listed by UniLED)
          (b"\x04", b"\x10", b"\x11", b"\x12", b"\x13", b"\x14", b"\x15", b"\x8e")),
    Model("SP617E", "SPI RGBW music controller", BanlanX2, 4, True, BANLANX_IDS, (b"\x17",)),
    Model("SP620E", "USB SPI RGB music controller", BanlanX2, 3, True, BANLANX_IDS, (b"\x1b\x10",)),
    Model("SP621E", "Mini SPI RGB controller", BanlanX2, 3, False, BANLANX_IDS, (b"\x0d", b"\x16")),
    Model("SP613E", "PWM RGB music controller", BanlanX3, 3, True, (20563,), (b"\x09\x00",)),
    Model("SP614E", "PWM RGBW music controller", BanlanX3, 4, True, (20563,), (b"\x0a",)),
    Model("SP623E", "Mini PWM RGB controller", BanlanX3, 3, False, (20563,), (b"\x0e\x00",)),
    Model("SP624E", "Mini PWM RGBW controller", BanlanX3, 4, False, (20563,), (b"\x0f\x00",)),
]

# Used when the model is unknown: the controller is probed with each protocol.
GENERIC_MODELS: list[Model] = [
    Model("BanlanX v2 (generic)", "BanlanX v2 compatible controller", BanlanX2, 3, True),
    Model("BanlanX v3 (generic)", "BanlanX v3 compatible controller", BanlanX3, 3, True),
]


def detect_model(manufacturer_data: dict[int, bytes] | None, name: str | None = None) -> Model | None:
    """Identifies the model from the manufacturer data, falling back to the advertised name (e.g. "SP611E")."""
    if manufacturer_data:
        if model := next((m for m in MODELS if m.matches(manufacturer_data)), None):
            return model
    if name:
        upper = name.strip().upper()
        return next((m for m in MODELS if upper.startswith(m.name)), None)
    return None


def model_by_name(name: str | None) -> Model | None:
    return next((m for m in MODELS + GENERIC_MODELS if m.name == name), None)


def looks_like_banlanx(name: str | None, manufacturer_data: dict[int, bytes] | None, service_uuids: list[str] | None) -> bool:
    if detect_model(manufacturer_data, name):
        return True
    if manufacturer_data and any(mid in BANLANX_IDS for mid in manufacturer_data):
        return True
    if service_uuids and any(u.lower()[:8] in ("0000ffe0", "0000e0ff") for u in service_uuids):
        return True
    return bool(name) and name.upper().startswith(("SP6", "SP1", "BANLAN"))


__all__ = [
    "AUDIO_INPUTS", "LIGHT_MODES", "MODELS", "GENERIC_MODELS", "DeviceState", "Effect", "Model",
    "Protocol", "BanlanX2", "BanlanX3", "chip_orders", "detect_model", "model_by_name", "looks_like_banlanx",
]
