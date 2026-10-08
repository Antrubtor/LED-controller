import asyncio
import importlib.util
import struct
from pathlib import Path

import numpy as np

from ledctl.audio.analyzer import Analyzer
from ledctl.audio.visualizers import VISUALIZERS, VisualParams
from ledctl.ble.controller import _CoalescingQueue
from ledctl.protocol import MODELS, chip_orders, detect_model, looks_like_banlanx, model_by_name

SP611E = model_by_name("SP611E")


def test_v2_catalogue_is_complete():
    p = SP611E.protocol(SP611E)
    dyn = p.dynamic_effects()
    assert len(dyn) == 142
    assert [e.id for e in dyn] == list(range(0x01, 0x8F))
    assert [e.id for e in p.sound_effects()] == list(range(0xC9, 0xDB))
    assert p.effect(0xC9).name == "Full Color Rhythm Spectrum"
    assert p.effect(0x0B).name == "Red Comet"
    assert len({e.name for e in p.effects()}) == len(p.effects()), "duplicate names"


def test_v2_commands():
    p = SP611E.protocol(SP611E)
    assert p.cmd_query() == bytes.fromhex("A0 70 00")
    assert p.cmd_power(True) == bytes.fromhex("A0 62 01 01")
    assert p.cmd_effect(0xBE) == bytes.fromhex("A0 63 01 BE")
    assert p.cmd_color(255, 0, 128, 200) == bytes.fromhex("A0 69 04 FF 00 80 C8")
    assert p.cmd_speed(99) == bytes.fromhex("A0 67 01 0A")  # clamped
    assert p.cmd_length(500) == bytes.fromhex("A0 68 01 96")
    assert p.cmd_sensitivity(0) == bytes.fromhex("A0 6B 01 01")


def test_v2_state_split_in_three_packets():
    """Frame captured from a real SP611E."""
    p = SP611E.protocol(SP611E)
    packets = [
        "53 43 01 1f 0f 01 00 be 02 ff 0a 3c aa 00 ff 00 10 09 04 0b",
        "53 43 02 1f 0f 14 1a 32 37 50 53 73 00 02 06 00 00 00 00 00",
        "53 43 03 1f 01 00",
    ]
    assert p.feed(bytes.fromhex(packets[0])) is None
    assert p.feed(bytes.fromhex(packets[1])) is None
    st = p.feed(bytes.fromhex(packets[2]))
    assert st.power is True and st.effect == 0xBE and st.light_mode == 0
    assert st.brightness == 255 and st.speed == 10 and st.length == 60
    assert st.rgb == (0xAA, 0x00, 0xFF) and st.sensitivity == 16 and st.chip_order == 2
    assert chip_orders(3)[st.chip_order] == "GRB"


def test_out_of_sequence_packet_is_ignored():
    p = SP611E.protocol(SP611E)
    assert p.feed(bytes.fromhex("53 43 02 1f 0f 14 1a 32 37 50 53 73 00 02 06 00 00 00 00 00")) is None


def test_v3():
    m = model_by_name("SP614E")
    p = m.protocol(m)
    assert p.cmd_color(1, 2, 3, 4) == bytes.fromhex("13 04 01 02 03 04")
    assert p.cmd_effect(0x63) == bytes.fromhex("15 01 63")
    assert p.effect_white == 0xCC
    msg = bytes.fromhex("01 ff 0a 00 65 00 00 ff ff 10 01 03 ff 00 00 00 ff 00 00 00 ff 00 01 40 00")
    st = p.feed(bytes([1, len(msg), len(msg)]) + msg)
    assert st.effect == 0x65 and st.brightness == 0xFF and st.rgb == (0, 0xFF, 0xFF) and st.white == 0x40


def test_model_detection():
    # Some SP611E advertise an 0x8E prefix that UniLED does not list: they are recognized by name.
    assert detect_model({20563: bytes.fromhex("8e10aabbccddeeff")}, "SP611E").name == "SP611E"
    assert detect_model({20563: bytes.fromhex("8e10aabbccddeeff")}).name == "SP611E"  # even without the name
    assert detect_model({20563: b"\x17\x10"}).name == "SP617E"
    assert detect_model({20563: b"\x0a\x21"}).name == "SP614E"
    assert detect_model({76: b"\x01"}, "Headphones") is None
    assert looks_like_banlanx(None, {20563: b"\x99"}, None)
    assert looks_like_banlanx(None, None, ["0000e0ff-0000-1000-8000-00805f9b34fb"])
    assert all(m.protocol(m).effects() for m in MODELS)


def test_coalescing_queue_keeps_latest_value_and_order():
    async def run():
        q = _CoalescingQueue()
        q.put(None, b"effect", True)
        q.put("color", b"c1", False)
        q.put("color", b"c2", False)
        q.put(None, b"speed", True)
        q.put("color", b"c3", False)
        out = []
        while (item := q.pop()) is not None:
            out.append(item[1])
        q.put("color", b"c4", False)  # the key is released once sent
        out.append(q.pop()[1])
        return out

    assert asyncio.run(run()) == [b"effect", b"c3", b"speed", b"c4"]


def test_analyzer_detects_beats_and_silence():
    a = Analyzer(48000)
    t = np.arange(2048) / 48000
    beats = 0
    for i in range(240):
        amp = 0.8 if i % 30 < 3 else 0.02
        f = a.process((amp * np.sin(2 * np.pi * 60 * t)).astype(np.float32), 1 / 60)
        beats += f.beat
    assert 6 <= beats <= 8
    for _ in range(60):
        f = a.process(np.zeros(2048, dtype=np.float32), 1 / 60)
    assert f.level < 0.05 and not f.beat


def test_visualizers_return_valid_values():
    a = Analyzer(48000)
    rng = np.random.default_rng(0)
    for cls in VISUALIZERS:
        v = cls()
        for _ in range(30):
            f = a.process(rng.normal(0, 0.2, 2048).astype(np.float32), 1 / 40)
            rgb, level = v.render(f, 1 / 40, VisualParams())
            assert all(0 <= c <= 255 for c in rgb) and 0.0 <= level <= 1.0, cls.__name__
            assert v.speed is None or 1 <= v.speed <= 10


def test_btsnoop(tmp_path):
    spec = importlib.util.spec_from_file_location("btsnoop", Path(__file__).parents[1] / "tools" / "btsnoop.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def acl(att: bytes) -> bytes:
        l2 = struct.pack("<HH", len(att), 4) + att
        return b"\x02" + struct.pack("<HH", 0x0040 | (0x2 << 12), len(l2)) + l2

    pkt = acl(bytes([0x52]) + struct.pack("<H", 0x13) + bytes.fromhex("A0 63 01 05"))
    log = tmp_path / "btsnoop_hci.log"
    log.write_bytes(b"btsnoop\0" + struct.pack(">II", 1, 1002)
                    + struct.pack(">IIIIq", len(pkt), len(pkt), 0, 0, 0x00E2_0000_0000_0000) + pkt)
    (ts, direction, pdu), = list(mod.att_packets(str(log)))
    assert direction == "→" and pdu == bytes.fromhex("52 13 00 A0 63 01 05")


def test_audio_feed_command():
    p = SP611E.protocol(SP611E)
    assert p.supports_audio_feed
    assert p.cmd_audio_feed([0, 128, 300]) == bytes.fromhex("A0 6D 03 00 80 FF")
    frame = p.cmd_audio_feed(list(range(40)))
    assert len(frame) == 3 + p.feed_bands <= 20  # one BLE write; longer frames hang the controller
    no_mic = model_by_name("SP621E")
    assert not no_mic.protocol(no_mic).supports_audio_feed
    v3 = model_by_name("SP614E")
    assert v3.protocol(v3).cmd_audio_feed([1, 2]) is None


def test_feed_levels_resampling():
    from ledctl.audio.engine import MusicEngine

    levels = MusicEngine._feed_levels(np.linspace(0, 1, 24), 16)
    assert len(levels) == 16 and levels[0] == 0 and levels[-1] == 255
    assert levels == sorted(levels)
    assert MusicEngine._feed_levels(np.zeros(0), 16) == []


def test_sound_effect_settings_match_the_app():
    p = SP611E.protocol(SP611E)
    colorable = {e.id for e in p.sound_effects() if e.colorable}
    with_length = {e.id for e in p.sound_effects() if e.has_length}
    assert colorable == {0xCA, 0xCC, 0xCE, 0xD0, 0xD2, 0xD4, 0xD6}
    assert with_length == {0xCD, 0xCE, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6}
    assert all(e.has_length for e in p.dynamic_effects())


def test_low_sensitivity_keeps_the_strip_darker():
    from ledctl.ui.pages import sensitivity_gain

    rng = np.random.default_rng(1)
    t = np.arange(2048) / 48000

    def mean_level(level: int) -> float:
        a = Analyzer(48000)
        a.sensitivity = sensitivity_gain(level)
        out = []
        for i in range(400):
            kick = 0.9 * np.exp(-(((i / 40) % 0.5) / 0.06)) * np.sin(2 * np.pi * 55 * t)
            f = a.process((kick + 0.12 * rng.normal(0, 1, 2048)).astype(np.float32), 1 / 40)
            out.append(f.level)
        return float(np.mean(out[80:]))

    low, mid, high = mean_level(1), mean_level(9), mean_level(16)
    assert low < 0.15 < mid < high
