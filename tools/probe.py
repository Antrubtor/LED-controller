"""Reverse-engineering helper for BanlanX controllers.

Examples:
    uv run tools/probe.py scan
    uv run tools/probe.py info AA:BB:CC:DD:EE:FF
    uv run tools/probe.py send AA:BB:CC:DD:EE:FF "A0 70 00"
    uv run tools/probe.py watch AA:BB:CC:DD:EE:FF            # print notifications (IR remote, button…)
    uv run tools/probe.py effects AA:BB:CC:DD:EE:FF --interactive --unknown-only
    uv run tools/probe.py opcodes AA:BB:CC:DD:EE:FF --i-understand   # brute-force unknown opcodes
    uv run tools/probe.py spectrum AA:BB:CC:DD:EE:FF          # explore the A0 6D audio feed format
    uv run tools/probe.py reset AA:BB:CC:DD:EE:FF             # back to solid white, built-in microphone

The SP611E stores any effect number it receives, so reading the state back (A0 70 00) does not
prove that an effect exists. Use `effects --interactive --unknown-only` to spot hidden effects by
eye. The initial state is always restored at the end.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from bleak import BleakClient, BleakScanner

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ledctl.protocol import GENERIC_MODELS, MODELS, DeviceState, Protocol, detect_model, looks_like_banlanx  # noqa: E402

WRITE_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"

# Tests that flash the whole strip draw large current spikes: keep them dim.
SAFE_BRIGHTNESS = 60
# 3 header bytes + payload must fit in one 20-byte BLE write; a longer frame (e.g. 32 bands) hangs the SP611E.
MAX_PAYLOAD = 17


class Probe:
    def __init__(self, client: BleakClient, protocol: Protocol, verbose: bool = True):
        self.client = client
        self.proto = protocol
        self.verbose = verbose
        self.state: DeviceState | None = None
        self.notifications: list[bytes] = []
        self._event = asyncio.Event()

    def on_notify(self, _char, data: bytearray) -> None:
        data = bytes(data)
        self.notifications.append(data)
        if self.verbose:
            print(f"  ← {data.hex(' ').upper()}")
        st = self.proto.feed(data)
        if st is not None:
            self.state = st
            self._event.set()

    async def write(self, data: bytes, response: bool = True) -> None:
        if self.verbose:
            print(f"  → {data.hex(' ').upper()}")
        await self.client.write_gatt_char(WRITE_UUID, data, response=response)

    async def query(self, timeout: float = 2.0) -> DeviceState | None:
        self._event.clear()
        self.state = None
        await self.write(self.proto.cmd_query())
        try:
            await asyncio.wait_for(self._event.wait(), timeout)
        except asyncio.TimeoutError:
            return None
        return self.state

    async def reconnect(self, attempts: int = 4) -> bool:
        """Reconnects after the controller dropped the link (it sometimes reboots on unknown commands)."""
        for i in range(attempts):
            try:
                device = await BleakScanner.find_device_by_address(self.address, timeout=10)
                if device is None:
                    raise ConnectionError("not found")
                self.client = BleakClient(device, timeout=20)
                await self.client.connect()
                await self.client.start_notify(WRITE_UUID, self.on_notify)
                print("  Reconnected.")
                return True
            except Exception as exc:  # noqa: BLE001
                print(f"  Reconnection attempt {i + 1}/{attempts} failed: {exc}")
                await asyncio.sleep(2)
        return False

    async def restore(self, st: DeviceState) -> None:
        if not self.client.is_connected and not await self.reconnect():
            print("⚠ Could not restore the initial state: the controller is unreachable.\n"
                  "  Power-cycle it, then run `reset` to bring it back to a sane state.")
            return
        p = self.proto
        cmds = [p.cmd_power(bool(st.power)), p.cmd_light_mode(st.light_mode or 0), p.cmd_effect(st.effect)]
        if st.rgb:
            cmds.append(p.cmd_color(*st.rgb, st.brightness or 255))
        cmds += [p.cmd_brightness(st.brightness or 255), p.cmd_speed(st.speed or 5)]
        if st.length and (c := p.cmd_length(st.length)):
            cmds.append(c)
        if st.sensitivity:
            cmds.append(p.cmd_sensitivity(st.sensitivity))
        if st.audio_input is not None:
            cmds.append(p.cmd_audio_input(st.audio_input))
        if st.chip_order is not None and (c := p.cmd_chip_order(st.chip_order)):
            cmds.append(c)
        # Commands sent right after a stream can be dropped by the controller: let it settle,
        # then check the state it reports and send everything again until it matches.
        await asyncio.sleep(0.5)
        fields = ("power", "light_mode", "effect", "brightness", "rgb", "audio_input")
        for attempt in range(1, 4):
            for c in cmds:
                await self.write(c)
                await asyncio.sleep(0.15)
            now = await self.query()
            wrong = [f for f in fields if now is None or getattr(now, f) != getattr(st, f)]
            if not wrong:
                print("Initial state restored.")
                return
            print(f"  Restore attempt {attempt}: still different ({', '.join(wrong)}), retrying…")
            await asyncio.sleep(0.5)
        print("⚠ The state could not be fully restored; run `reset` or set it back in the app.")


async def open_probe(address: str, verbose: bool = True):
    device = await BleakScanner.find_device_by_address(address, timeout=10)
    if device is None:
        raise SystemExit(f"Device {address} not found (powered on? still connected to the mobile app?)")
    model = next((m for m in MODELS if device.name and device.name.upper().startswith(m.name)), GENERIC_MODELS[0])
    client = BleakClient(device, timeout=20)
    await client.connect()
    probe = Probe(client, model.protocol(model), verbose)
    probe.address = address
    await client.start_notify(WRITE_UUID, probe.on_notify)
    print(f"Connected to {device.name} ({address}) — protocol {probe.proto.family}")
    return probe


# ---------------------------------------------------------------------------- commands
async def cmd_scan(args) -> None:
    print(f"Scanning for {args.timeout} s…")
    found = await BleakScanner.discover(timeout=args.timeout, return_adv=True)
    for addr, (dev, adv) in sorted(found.items(), key=lambda kv: -kv[1][1].rssi):
        name = adv.local_name or dev.name
        ok = looks_like_banlanx(name, adv.manufacturer_data, adv.service_uuids)
        if not ok and not args.all:
            continue
        model = detect_model(adv.manufacturer_data, name)
        manu = {hex(k): v.hex(" ") for k, v in adv.manufacturer_data.items()}
        print(f"{'★' if ok else ' '} {addr}  {name or '?':24} {adv.rssi:4} dBm  model={model.name if model else '?'}")
        print(f"      manufacturer data={manu}  services={adv.service_uuids}")


async def cmd_info(args) -> None:
    probe = await open_probe(args.address)
    try:
        for s in probe.client.services:
            print(f"service {s.uuid}")
            for c in s.characteristics:
                print(f"    {c.uuid}  handle={c.handle}  {','.join(c.properties)}")
        st = await probe.query()
        print("\nDecoded state:", st)
        if st:
            print("Raw bytes:", st.raw.hex(" ").upper())
    finally:
        await probe.client.disconnect()


async def cmd_send(args) -> None:
    probe = await open_probe(args.address)
    try:
        for chunk in args.hex:
            await probe.write(bytes.fromhex(chunk.replace("0x", "")), response=not args.no_response)
            await asyncio.sleep(args.wait)
    finally:
        await probe.client.disconnect()


async def cmd_watch(args) -> None:
    probe = await open_probe(args.address)
    print("Listening for notifications (Ctrl+C to quit)…")
    try:
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await probe.client.disconnect()


async def cmd_effects(args) -> None:
    probe = await open_probe(args.address, verbose=args.verbose)
    known = {e.id for e in probe.proto.effects()}
    original = await probe.query()
    if original is None:
        raise SystemExit("The controller does not report its state: cannot validate effects.")
    ids = [e for e in range(args.start, args.end + 1) if not (args.unknown_only and e in known)]
    print(f"Initial state: effect {original.effect:#04x}. {len(ids)} number(s) to test.")
    if args.interactive:
        print("For each number, look at the LEDs and type:  [Enter] nothing new · "
              "v visible/new effect · n dark/off · q quit")
    accepted, rejected, visible, dark = [], [], [], []
    try:
        await probe.write(probe.proto.cmd_light_mode(0))
        for eid in ids:
            await probe.write(probe.proto.cmd_effect(eid))
            await asyncio.sleep(args.delay)
            st = await probe.query()
            ok = st is not None and st.effect == eid
            (accepted if ok else rejected).append(eid)
            tag = "catalogue" if eid in known else ""
            line = f"  {eid:#04x}  {'stored  ' if ok else 'rejected'}  {tag}"
            if not args.interactive:
                print(line + ("" if ok or st is None else f"  (controller stays on {st.effect:#04x})"))
                continue
            answer = (await asyncio.to_thread(input, line + "  > ")).strip().lower()
            if answer == "q":
                break
            if answer == "v":
                visible.append(eid)
            elif answer == "n":
                dark.append(eid)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("Interrupted.")
    finally:
        print("Restoring the initial state…")
        await probe.restore(original)
        await probe.client.disconnect()

    if len(accepted) == len(ids) and not args.interactive:
        print("\n⚠ The controller stores any number: its state does not prove that an effect exists.\n"
              "  Run again with --interactive --unknown-only to spot hidden effects by eye.")
    else:
        print(f"\n{len(accepted)} number(s) stored, {len(rejected)} rejected.")
    if args.interactive:
        print(f"Reported visible effects: {[hex(e) for e in visible]}")
        print(f"Numbers that turn the strip dark: {[hex(e) for e in dark]}")
    out = Path(args.output)
    out.write_text(json.dumps({"accepted": accepted, "rejected": rejected, "visible": visible, "dark": dark}, indent=1))
    print(f"Results saved to {out}")


KNOWN_V2_OPCODES = {0x62, 0x63, 0x64, 0x66, 0x67, 0x68, 0x69, 0x6A, 0x6B, 0x6C, 0x70, 0x76}


async def cmd_opcodes(args) -> None:
    if not args.i_understand:
        raise SystemExit(
            "⚠ Sending unknown opcodes may change the controller configuration (LED count, chip type,\n"
            "Bluetooth name…). The current state is restored after each try, but not necessarily those\n"
            "internal settings. Run again with --i-understand to continue (and keep the mobile app at hand)."
        )
    probe = await open_probe(args.address, verbose=args.verbose)
    baseline = await probe.query()
    if baseline is None:
        raise SystemExit("No reference state.")
    skip = KNOWN_V2_OPCODES if not args.include_known else set()
    findings = []
    try:
        for op in range(args.start, args.end + 1):
            if op in skip:
                continue
            payload = bytes([0xA0, op, 1, args.value])
            probe.notifications.clear()
            await probe.write(payload, response=True)
            await asyncio.sleep(args.delay)
            extra = list(probe.notifications)
            st = await probe.query()
            diff = []
            if st is not None:
                diff = [(i, a, b) for i, (a, b) in enumerate(zip(baseline.raw, st.raw)) if a != b]
            label = ", ".join(f"byte {i}: {a:#04x}→{b:#04x}" for i, a, b in diff) or "no state change"
            reply = f"  direct reply: {[n.hex(' ') for n in extra]}" if extra else ""
            print(f"  A0 {op:02X} 01 {args.value:02X}  →  {label}{reply}")
            if diff or extra:
                findings.append({"opcode": op, "diff": diff, "replies": [n.hex() for n in extra]})
                await probe.restore(baseline)
                await asyncio.sleep(0.2)
    except KeyboardInterrupt:
        print("Interrupted.")
    finally:
        await probe.restore(baseline)
        await probe.client.disconnect()
    Path(args.output).write_text(json.dumps(findings, indent=1))
    print(f"\n{len(findings)} opcode(s) with an observable effect, details in {args.output}")


async def cmd_feed(args) -> None:
    """Looks for the "phone microphone" audio feed command.

    The controller is put on a built-in sound effect with the "Player" audio input, which makes it
    wait for audio data from the app instead of listening to its microphone. Each candidate opcode
    is then streamed with a square wave (0.5 s at full level, 0.5 s silent): the right one makes
    the strip blink exactly once per second, a pattern that ambient sound cannot produce.

    Caveat: after ~10 s without valid data the controller starts a built-in demo animation, which
    can be mistaken for a reaction to a later opcode. Only a result that repeats across runs counts.
    Known result on an SP611E: A0 6D is the audio feed (see the `spectrum` command).
    """
    if not args.i_understand:
        raise SystemExit(
            "⚠ This streams unknown opcodes to the controller. One of them may make it drop the connection\n"
            "or change an internal setting. Run again with --i-understand to continue; `reset` restores a\n"
            "sane state afterwards."
        )
    probe = await open_probe(args.address, verbose=args.verbose)
    p = probe.proto
    baseline = await probe.query()
    if baseline is None:
        raise SystemExit("No reference state.")
    opcodes = [int(x, 0) for x in args.opcodes.split(",")]
    lengths = [min(int(x), MAX_PAYLOAD) for x in args.lengths.split(",")]
    hits = []

    async def ask(prompt: str) -> str:
        return (await asyncio.to_thread(input, prompt)).strip().lower()

    try:
        for cmd in (p.cmd_light_mode(0), p.cmd_effect(args.effect), p.cmd_audio_input(args.input),
                    p.cmd_sensitivity(p.max_sensitivity), p.cmd_brightness(SAFE_BRIGHTNESS)):
            await probe.write(cmd)
            await asyncio.sleep(0.1)
        st = await probe.query()
        print(f"Effect {args.effect:#04x}, audio input reported by the controller: "
              f"{st.audio_input if st else '?'} (requested {args.input}).")
        print("\nStep 1 - control: nothing is sent for 5 seconds. Keep the room quiet and watch the strip.")
        await asyncio.sleep(5)
        if await ask("  Did the strip stay dark/still? [y/n] > ") != "y":
            print("  ⚠ The controller is still reacting to something (probably its own microphone):\n"
                  "    results below may be ambiguous. Try again with --input 2, or cover the microphone.")
        print("\nStep 2 - each opcode is streamed for a few seconds as a square wave:\n"
              "  the right command makes the strip blink ON/OFF exactly once per second.\n"
              "  After each one: y = it blinked once per second, Enter = no, q = quit.\n")
        for op in opcodes:
            print(f"  Sending A0 {op:02X}…", flush=True)
            for n in lengths:
                t0 = time.perf_counter()
                while (t := time.perf_counter() - t0) < args.seconds:
                    level = 255 if (t % 1.0) < 0.5 else 0
                    payload = [level * (i + 1) // n if args.sweep else level for i in range(n)]
                    await probe.write(bytes([0xA0, op, n, *payload]), response=False)
                    await asyncio.sleep(1 / args.rate)
            answer = await ask(f"  A0 {op:02X}: blinked once per second? > ")
            if answer == "q":
                break
            if answer == "y":
                hits.append(op)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("Interrupted.")
    finally:
        print("Restoring the initial state…")
        await probe.restore(baseline)
        await probe.client.disconnect()
    print(f"\nOpcodes that made the strip react: {[hex(o) for o in hits]}")


async def cmd_reset(args) -> None:
    """Puts the controller back into a sane state: solid color, built-in microphone, single-effect mode."""
    probe = await open_probe(args.address, verbose=True)
    p = probe.proto
    try:
        for cmd in (p.cmd_power(True), p.cmd_light_mode(0), p.cmd_audio_input(0), p.cmd_effect(p.effect_solid),
                    p.cmd_color(255, 255, 255, args.brightness), p.cmd_brightness(args.brightness)):
            await probe.write(cmd)
            await asyncio.sleep(0.1)
        print("Decoded state:", await probe.query())
    finally:
        await probe.client.disconnect()


SPECTRUM_PHASES = [
    ("pulse16", 16, "All 16 bands blink: 0.5 s full, 0.5 s silent.",
     "Does the strip blink ON/OFF exactly once per second?"),
    ("sweep8", 8, "A single 'loud' band moves from the first band to the last one, then starts over.",
     "Does something move along the strip? Describe it (direction, position, speed)."),
    ("sweep16", 16, "Same moving band, with 16 bands.",
     "Same question: does it move, and does it look different from the previous step?"),
    ("levels16", 16, "All bands alternate every 2 s between a quiet level (64) and a loud level (255).",
     "Do you see two clearly different intensities (quiet vs loud)?"),
    ("ramp16", 16, "Static ramp: band 1 is silent, band 16 is the loudest.",
     "Is the display stable? Does one side/end of the strip look 'louder' than the other?"),
]


def _spectrum_payload(phase: str, n: int, t: float) -> list[int]:
    if phase.startswith("pulse"):
        return [255 if (t % 1.0) < 0.5 else 0] * n
    if phase.startswith("sweep"):
        pos = int(t / 0.3) % n
        return [255 if i == pos else 0 for i in range(n)]
    if phase.startswith("levels"):
        return [64 if (t % 4.0) < 2.0 else 255] * n
    return [round(255 * i / (n - 1)) for i in range(n)]


async def cmd_spectrum(args) -> None:
    """Explores the payload format of A0 6D, the audio feed command used by the phone-microphone mode.

    Data is streamed continuously from a background task, even while you type, so the controller
    never falls back to its built-in demo animation (it starts after ~10 s without data).
    """
    probe = await open_probe(args.address, verbose=False)
    p = probe.proto
    baseline = await probe.query()
    if baseline is None:
        raise SystemExit("No reference state.")
    print("Initial state (keep it, `reset` can bring the controller back if needed):", baseline)
    current = {"phase": "pulse16", "n": 16, "t0": time.perf_counter()}
    answers: dict[str, str] = {}

    async def streamer() -> None:
        while True:
            t = time.perf_counter() - current["t0"]
            payload = _spectrum_payload(current["phase"], min(current["n"], MAX_PAYLOAD), t)
            try:
                await probe.write(bytes([0xA0, args.opcode, len(payload), *payload]), response=False)
            except Exception:  # noqa: BLE001 - the link dropped; the main task will notice
                return
            await asyncio.sleep(1 / args.rate)

    task = None
    try:
        for cmd in (p.cmd_light_mode(0), p.cmd_effect(args.effect), p.cmd_audio_input(1),
                    p.cmd_sensitivity(p.max_sensitivity), p.cmd_brightness(args.brightness)):
            await probe.write(cmd)
            await asyncio.sleep(0.1)
        task = asyncio.create_task(streamer())
        print(f"\nStreaming A0 {args.opcode:02X} on effect {args.effect:#04x} (brightness {args.brightness}).")
        print("For each step: watch for ~5 s, then answer in a few words (or q to stop).\n")
        for name, n, what, question in SPECTRUM_PHASES:
            current.update(phase=name, n=n, t0=time.perf_counter())
            print(f"[{name}] {what}")
            answer = (await asyncio.to_thread(input, f"  {question}\n  > ")).strip()
            if answer.lower() == "q":
                break
            answers[name] = answer
            if task.done():
                print("⚠ The link dropped during this step.")
                break
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("Interrupted.")
    finally:
        if task:
            task.cancel()
        print("Restoring the initial state…")
        await probe.restore(baseline)
        if probe.client.is_connected:
            await probe.client.disconnect()
    Path(args.output).write_text(json.dumps(answers, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\nAnswers saved to {args.output}:")
    for k, v in answers.items():
        print(f"  {k}: {v}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan", help="list nearby controllers")
    p.add_argument("--all", action="store_true", help="also list unrecognized devices")
    p.add_argument("--timeout", type=float, default=8)

    p = sub.add_parser("info", help="GATT services + decoded state")
    p.add_argument("address")

    p = sub.add_parser("send", help="send raw bytes")
    p.add_argument("address")
    p.add_argument("hex", nargs="+", help='e.g. "A0 63 01 05"')
    p.add_argument("--wait", type=float, default=1.0)
    p.add_argument("--no-response", action="store_true")

    p = sub.add_parser("watch", help="print notifications continuously")
    p.add_argument("address")

    p = sub.add_parser("effects", help="brute-force effect numbers")
    p.add_argument("address")
    p.add_argument("--start", type=lambda x: int(x, 0), default=0x00)
    p.add_argument("--end", type=lambda x: int(x, 0), default=0xFF)
    p.add_argument("--delay", type=float, default=0.3)
    p.add_argument("--interactive", action="store_true", help="ask for each number whether an effect is visible")
    p.add_argument("--unknown-only", action="store_true", help="only test numbers missing from the catalogue")
    p.add_argument("--output", default="effects_scan.json")
    p.add_argument("-v", "--verbose", action="store_true")

    p = sub.add_parser("opcodes", help="brute-force A0 xx opcodes (careful)")
    p.add_argument("address")
    p.add_argument("--start", type=lambda x: int(x, 0), default=0x50)
    p.add_argument("--end", type=lambda x: int(x, 0), default=0x9F)
    p.add_argument("--value", type=lambda x: int(x, 0), default=0x01)
    p.add_argument("--delay", type=float, default=0.4)
    p.add_argument("--include-known", action="store_true")
    p.add_argument("--i-understand", action="store_true")
    p.add_argument("--output", default="opcodes_scan.json")
    p.add_argument("-v", "--verbose", action="store_true")

    p = sub.add_parser("feed", help="look for the phone-microphone audio feed command (watch the strip)")
    p.add_argument("address")
    p.add_argument("--opcodes", default="0x6d,0x6e,0x6f,0x65,0x71,0x72,0x73,0x74,0x75,0x77,0x78,0x79,0x7a,0x7b,0x7c,0x7d,0x7e,0x7f,0x61,0x60")
    p.add_argument("--lengths", default="1,16", help="payload lengths to try for each opcode")
    p.add_argument("--effect", type=lambda x: int(x, 0), default=0xC9, help="sound effect used for the test")
    p.add_argument("--input", type=int, default=1, help="audio input to select (1 = Player/phone, 2 = external)")
    p.add_argument("--seconds", type=float, default=3.0, help="streaming time per payload length")
    p.add_argument("--rate", type=float, default=30, help="packets per second")
    p.add_argument("--sweep", action="store_true", help="give each band a different level")
    p.add_argument("--i-understand", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")

    p = sub.add_parser("reset", help="put the controller back into a sane state (solid white, built-in mic)")
    p.add_argument("address")
    p.add_argument("--brightness", type=int, default=128)

    p = sub.add_parser("spectrum", help="explore the A0 6D audio feed format (watch the strip)")
    p.add_argument("address")
    p.add_argument("--opcode", type=lambda x: int(x, 0), default=0x6D)
    p.add_argument("--effect", type=lambda x: int(x, 0), default=0xC9, help="sound effect used for the test")
    p.add_argument("--brightness", type=int, default=SAFE_BRIGHTNESS, help="kept low to limit current and heat")
    p.add_argument("--rate", type=float, default=25, help="packets per second")
    p.add_argument("--output", default="spectrum_test.json")

    args = ap.parse_args()
    handler = {"scan": cmd_scan, "info": cmd_info, "send": cmd_send, "watch": cmd_watch,
               "effects": cmd_effects, "opcodes": cmd_opcodes, "feed": cmd_feed,
               "reset": cmd_reset, "spectrum": cmd_spectrum}[args.cmd]
    t = time.perf_counter()
    try:
        asyncio.run(handler(args))
    except KeyboardInterrupt:
        pass
    print(f"({time.perf_counter() - t:.1f} s)")


if __name__ == "__main__":
    main()
