"""Extracts Bluetooth LE traffic (GATT writes and notifications) from an Android HCI log.

This is the most reliable way to find out what the BanlanX mobile app sends
(for instance in the phone microphone music mode):

1. On the phone: Developer options → "Enable Bluetooth HCI snoop log".
2. Toggle Bluetooth off/on, then use the app (note the time of each action).
3. Fetch the log: `adb bugreport report.zip` (file FS/data/misc/bluetooth/logs/btsnoop_hci.log)
4. `uv run tools/btsnoop.py btsnoop_hci.log`            (every write)
   `uv run tools/btsnoop.py btsnoop_hci.log --notify`   (with the controller replies)
   `uv run tools/btsnoop.py btsnoop_hci.log --stats`    (how often each command is sent)
"""

from __future__ import annotations

import argparse
import struct
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

ATT_OPCODES = {
    0x12: "WRITE_REQ",
    0x52: "WRITE_CMD",
    0x1B: "NOTIFY",
    0x1D: "INDICATE",
    0x0B: "READ_RSP",
}
UNIX_OFFSET_US = 0x00DCDDB30F2F8000  # btsnoop timestamps count µs since year 0


def records(path: str):
    with open(path, "rb") as f:
        header = f.read(16)
        if header[:8] != b"btsnoop\0":
            raise SystemExit("This file is not a btsnoop log.")
        _version, datalink = struct.unpack(">II", header[8:16])
        while True:
            rec = f.read(24)
            if len(rec) < 24:
                return
            _orig, incl, flags, _drops, ts = struct.unpack(">IIIIq", rec)
            data = f.read(incl)
            yield datalink, flags, ts, data


def att_packets(path: str):
    """Reassembles ACL → L2CAP packets and yields ATT PDUs (channel 0x0004)."""
    pending: dict[int, bytearray] = defaultdict(bytearray)
    expected: dict[int, int] = {}
    for datalink, flags, ts, data in records(path):
        if datalink == 1002:  # H4: first byte = packet type
            if not data or data[0] != 0x02:
                continue
            data = data[1:]
        elif datalink == 1001:
            if flags & 0x02:  # command/event, no data
                continue
        else:
            continue
        if len(data) < 4:
            continue
        hdr, length = struct.unpack("<HH", data[:4])
        handle, pb = hdr & 0x0FFF, (hdr >> 12) & 0x3
        payload = data[4 : 4 + length]
        direction = "←" if flags & 0x01 else "→"
        key = (handle << 1) | (flags & 1)
        if pb in (0x0, 0x2):  # start of an L2CAP frame
            if len(payload) < 4:
                continue
            l2len = struct.unpack("<H", payload[:2])[0]
            pending[key] = bytearray(payload)
            expected[key] = l2len + 4
        elif pb == 0x1 and key in pending:  # continuation
            pending[key] += payload
        else:
            continue
        buf = pending[key]
        if len(buf) < expected.get(key, 1 << 30):
            continue
        cid = struct.unpack("<H", buf[2:4])[0]
        pdu = bytes(buf[4 : expected[key]])
        pending.pop(key, None)
        if cid == 0x0004 and pdu:
            yield ts, direction, pdu


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log")
    ap.add_argument("--notify", action="store_true", help="also print the controller notifications")
    ap.add_argument("--handle", type=lambda x: int(x, 0), help="only keep one GATT handle")
    ap.add_argument("--stats", action="store_true", help="summary per command (first 2 bytes)")
    args = ap.parse_args()

    stats: Counter[str] = Counter()
    first_ts = None
    for ts, direction, pdu in att_packets(args.log):
        op = pdu[0]
        if op not in ATT_OPCODES or len(pdu) < 3:
            continue
        name = ATT_OPCODES[op]
        if name in ("NOTIFY", "INDICATE", "READ_RSP") and not args.notify:
            continue
        handle = struct.unpack("<H", pdu[1:3])[0] if name != "READ_RSP" else 0
        if args.handle is not None and handle != args.handle:
            continue
        value = pdu[3:] if name != "READ_RSP" else pdu[1:]
        first_ts = first_ts if first_ts is not None else ts
        when = datetime(1970, 1, 1) + timedelta(microseconds=ts - UNIX_OFFSET_US)
        if args.stats:
            stats[value[:2].hex(" ").upper() if name.startswith("WRITE") else f"notify {value[:2].hex(' ')}"] += 1
        else:
            print(f"{when:%H:%M:%S.%f}"[:-3] + f"  +{(ts - first_ts) / 1e6:8.3f}s  {direction} {name:9} "
                  f"h={handle:#06x}  {value.hex(' ').upper()}")
    if args.stats:
        for k, n in stats.most_common():
            print(f"{n:6}×  {k}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
