"""Bluetooth handling: scanning, connecting/disconnecting and smooth command delivery.

All bleak code runs in a dedicated asyncio loop (background thread); the UI talks to it
through Qt signals and thread-safe calls.

Writes go through a *coalescing* queue: a "streamed" command (a slider being dragged, a
music frame…) replaces the pending one with the same key if it has not been sent yet. The
controller therefore always receives the most recent value and latency never piles up.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum

from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from PySide6.QtCore import QObject, Signal

from ..protocol import GENERIC_MODELS, Model, Protocol, detect_model, looks_like_banlanx

log = logging.getLogger(__name__)

WRITE_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"
MIN_INTERVAL = 0.04  # never write faster than this, whatever the settings say


class Link(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    DISCONNECTING = "disconnecting"


@dataclass
class Discovered:
    address: str
    name: str
    rssi: int
    model: Model | None
    compatible: bool
    manufacturer_data: dict[int, bytes] = field(default_factory=dict)


class _CoalescingQueue:
    """FIFO queue where a keyed entry is replaced in place by the most recent value."""

    def __init__(self) -> None:
        self._items: deque[list] = deque()
        self._by_key: dict[str, list] = {}
        self._event = asyncio.Event()

    def put(self, key: str | None, data: bytes, response: bool) -> None:
        if key is not None and (item := self._by_key.get(key)) is not None:
            item[1] = data
            return
        item = [key, data, response]
        self._items.append(item)
        if key is not None:
            self._by_key[key] = item
        self._event.set()

    def drop(self, key: str) -> None:
        if (item := self._by_key.pop(key, None)) is not None:
            self._items.remove(item)

    def clear(self) -> None:
        self._items.clear()
        self._by_key.clear()

    async def wait(self) -> None:
        while not self._items:
            self._event.clear()
            await self._event.wait()

    def pop(self) -> tuple[str | None, bytes, bool] | None:
        if not self._items:
            return None
        key, data, response = self._items.popleft()
        if key is not None:
            self._by_key.pop(key, None)
        return key, data, response


class BleController(QObject):
    scan_result = Signal(object)  # Discovered
    scan_running = Signal(bool)
    link_changed = Signal(str, str)  # Link, message
    connected = Signal(object, str)  # Protocol, address
    state_received = Signal(object)  # DeviceState
    notification = Signal(bytes)
    error = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        # The BLE link carries about 20 writes/s. Writing faster does not go faster: unacknowledged writes pile
        # up in the Windows Bluetooth stack, and every later command waits behind that backlog for seconds.
        self.min_interval = 0.05
        # One-off commands (effect, audio input…) need breathing room: the controller silently ignores
        # a command that arrives right after another one.
        self.command_gap = 0.12
        self.auto_reconnect = True
        self.link = Link.DISCONNECTED
        self.protocol: Protocol | None = None
        self.address: str | None = None

        self._devices: dict[str, BLEDevice] = {}
        self._models: dict[str, Model] = {}
        self._client: BleakClient | None = None
        self._write_char: BleakGATTCharacteristic | None = None
        self._queue: _CoalescingQueue | None = None
        self._writer: asyncio.Task | None = None
        self._scan_task: asyncio.Task | None = None
        self._connect_task: asyncio.Task | None = None
        self._user_disconnect = False
        self._state_event: asyncio.Event | None = None
        self._last_write = 0.0
        self._gap = 0.0

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="ble-loop", daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------ public API
    def scan(self, timeout: float = 8.0) -> None:
        self._submit(self._scan(timeout))

    def stop_scan(self) -> None:
        self._loop.call_soon_threadsafe(lambda: self._scan_task and self._scan_task.cancel())

    def connect_device(self, address: str, model: Model | None = None) -> None:
        if model is not None:
            self._models[address] = model
        self._submit(self._start_connect(address))

    def disconnect_device(self) -> None:
        self._submit(self._disconnect(user=True))

    def send(self, data: bytes | None, key: str | None = None) -> None:
        """Sends a command. With `key`, only the most recent value is transmitted."""
        if data:
            self._loop.call_soon_threadsafe(self._enqueue, key, bytes(data))

    def drop(self, key: str) -> None:
        self._loop.call_soon_threadsafe(lambda: self._queue and self._queue.drop(key))

    def query_state(self) -> None:
        if self.protocol:
            self.send(self.protocol.cmd_query())

    @property
    def is_connected(self) -> bool:
        return self.link == Link.CONNECTED

    def shutdown(self) -> None:
        if not self._loop.is_running():
            return
        try:
            asyncio.run_coroutine_threadsafe(self._disconnect(user=True), self._loop).result(timeout=4)
        except Exception:  # noqa: BLE001 - we are quitting anyway
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=2)

    # ------------------------------------------------------------------ internals
    def _submit(self, coro) -> None:
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    def _set_link(self, link: Link, message: str = "") -> None:
        self.link = link
        self.link_changed.emit(link.value, message)

    def _enqueue(self, key: str | None, data: bytes) -> None:
        if self._queue is not None and self.link == Link.CONNECTED:
            self._queue.put(key, data, response=key is None)

    async def _scan(self, timeout: float) -> None:
        if self._scan_task and not self._scan_task.done():
            return
        self._scan_task = asyncio.current_task()
        self.scan_running.emit(True)

        def on_detect(device: BLEDevice, adv) -> None:
            self._devices[device.address] = device
            model = detect_model(adv.manufacturer_data, adv.local_name or device.name)
            if model:
                self._models[device.address] = model
            self.scan_result.emit(
                Discovered(
                    address=device.address,
                    name=adv.local_name or device.name or "Unknown device",
                    rssi=adv.rssi,
                    model=model,
                    compatible=looks_like_banlanx(adv.local_name or device.name, adv.manufacturer_data, adv.service_uuids),
                    manufacturer_data=dict(adv.manufacturer_data),
                )
            )

        try:
            async with BleakScanner(on_detect):
                await asyncio.sleep(timeout)
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001
            log.exception("Scan failed")
            self.error.emit(f"Bluetooth scan failed: {exc}")
        finally:
            self._scan_task = None
            self.scan_running.emit(False)

    async def _start_connect(self, address: str) -> None:
        if self._connect_task and not self._connect_task.done():
            return
        if self._client is not None:
            await self._disconnect(user=True)
        if self._scan_task:
            self._scan_task.cancel()
        self._user_disconnect = False
        self._connect_task = asyncio.current_task()
        try:
            await self._connect(address)
        finally:
            self._connect_task = None

    async def _connect(self, address: str, reconnecting: bool = False) -> bool:
        self._set_link(Link.RECONNECTING if reconnecting else Link.CONNECTING, address)
        self.address = address
        try:
            target = self._devices.get(address)
            if target is None:
                target = await self._find(address)
            if target is None:
                raise ConnectionError("Device not found. Is it powered on and in range?")
            client = BleakClient(target, disconnected_callback=self._on_disconnected, timeout=20)
            await client.connect()
            self._client = client

            write_char, notify_char = self._resolve_characteristics(client)
            self._write_char = write_char
            if notify_char is not None:
                await client.start_notify(notify_char, self._on_notify)

            self._queue = _CoalescingQueue()
            self._last_write = 0.0
            self._writer = asyncio.create_task(self._write_loop(client))

            self.protocol = await self._resolve_protocol(client, address, target)
            # `connected` before `link_changed`: the UI already knows the protocol when it shows "Connected".
            self.link = Link.CONNECTED
            self.connected.emit(self.protocol, address)
            self._set_link(Link.CONNECTED, address)
            self.send(self.protocol.cmd_query())
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("Connection failed: %s", exc)
            await self._cleanup()
            if not reconnecting:
                self._set_link(Link.DISCONNECTED, "")
                self.error.emit(f"Could not connect: {exc}")
            return False

    @staticmethod
    def _resolve_characteristics(client: BleakClient):
        write_char = client.services.get_characteristic(WRITE_UUID)
        chars = [c for s in client.services for c in s.characteristics]
        if write_char is None:
            write_char = next((c for c in chars if {"write", "write-without-response"} & set(c.properties)), None)
        if write_char is None:
            raise ConnectionError("No writable characteristic: this is not a compatible controller.")
        notify_char = write_char if "notify" in write_char.properties else None
        if notify_char is None:
            notify_char = next(
                (c for c in chars if "notify" in c.properties and c.service_uuid == write_char.service_uuid), None
            )
        return write_char, notify_char

    async def _find(self, address: str, timeout: float = 10.0) -> BLEDevice | None:
        """Looks for the device and keeps its advertisement, which identifies the model.

        The name often comes in a second packet (scan response), so once the device is seen we keep
        listening a little for an advertisement that identifies the model.
        """
        found: dict = {}
        identified = asyncio.Event()

        def on_detect(device: BLEDevice, adv) -> None:
            if device.address.upper() != address.upper():
                return
            found["device"] = device
            model = detect_model(adv.manufacturer_data, adv.local_name or device.name)
            if model is not None:
                found["model"] = model
                identified.set()

        async with BleakScanner(on_detect):
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout
            while "device" not in found and loop.time() < deadline:
                await asyncio.sleep(0.1)
            if "device" in found:
                try:
                    await asyncio.wait_for(identified.wait(), timeout=3)
                except asyncio.TimeoutError:
                    pass
        if found.get("model") is not None:
            self._models[address] = found["model"]
        return found.get("device")

    async def _resolve_protocol(self, client: BleakClient, address: str, device: BLEDevice) -> Protocol:
        model = self._models.get(address)
        if model is None or model in GENERIC_MODELS:
            model = detect_model(None, device.name) or model
        if model is not None:
            return model.protocol(model)
        # Unknown model: query the controller with each protocol until one answers.
        for candidate in GENERIC_MODELS:
            proto = candidate.protocol(candidate)
            self.protocol = proto
            self._state_event = asyncio.Event()
            try:
                await client.write_gatt_char(self._write_char, proto.cmd_query(), response=None)
                await asyncio.wait_for(self._state_event.wait(), timeout=1.5)
                log.info("Detected protocol: %s", proto.family)
                return proto
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                continue
            finally:
                self._state_event = None
        return GENERIC_MODELS[0].protocol(GENERIC_MODELS[0])

    async def _write_loop(self, client: BleakClient) -> None:
        props = set(self._write_char.properties)
        can_fast = "write-without-response" in props
        queue = self._queue
        while True:
            await queue.wait()
            # Wait *before* popping: meanwhile a newer value can replace the pending one.
            delay = self._last_write + self._gap - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
            item = queue.pop()
            if item is None:
                continue
            key, data, ordered = item
            # Write-without-response whenever possible: BLE still guarantees delivery at the link layer, and
            # the SP611E stops acknowledging writes after an audio stream, which would stall the queue.
            response = not can_fast
            try:
                # Right after a stream the controller sometimes never acknowledges a write. Waiting would
                # freeze the whole queue, and asyncio.wait_for() would hang too (the WinRT operation ignores
                # cancellation): give up after 1 s without awaiting it, and resend the data unacknowledged.
                write = asyncio.ensure_future(client.write_gatt_char(self._write_char, data, response=response))
                done, _ = await asyncio.wait({write}, timeout=1.0)
                if done:
                    write.result()
                else:
                    write.cancel()
                    write.add_done_callback(lambda t: t.cancelled() or t.exception())
                    log.debug("No acknowledgement for %s, resending without response", data.hex(" "))
                    if can_fast:
                        await client.write_gatt_char(self._write_char, data, response=False)
            except Exception as exc:  # noqa: BLE001
                log.debug("Write failed (%s): %s", data.hex(" "), exc)
                if not client.is_connected:
                    return
            self._last_write = time.perf_counter()
            self._gap = max(self.command_gap, self.min_interval) if ordered else max(self.min_interval, MIN_INTERVAL)

    def _on_notify(self, _char, data: bytearray) -> None:
        self.notification.emit(bytes(data))
        if self.protocol is None:
            return
        try:
            state = self.protocol.feed(bytes(data))
        except Exception:  # noqa: BLE001
            log.exception("Unreadable notification: %s", bytes(data).hex(" "))
            return
        if state is not None:
            if self._state_event is not None:
                self._state_event.set()
            self.state_received.emit(state)

    def _on_disconnected(self, client: BleakClient) -> None:
        # Called by bleak inside the asyncio loop.
        if client is not self._client:
            return
        if self._user_disconnect or self.link == Link.DISCONNECTING:
            return
        asyncio.ensure_future(self._handle_lost(self.address))

    async def _handle_lost(self, address: str | None) -> None:
        await self._cleanup()
        if not (self.auto_reconnect and address):
            self._set_link(Link.DISCONNECTED, "Connection lost")
            return
        for attempt, delay in enumerate((1, 3, 6, 10), start=1):
            if self._user_disconnect:
                return
            self._set_link(Link.RECONNECTING, f"Connection lost, retrying ({attempt}/4)…")
            await asyncio.sleep(delay)
            if self._user_disconnect:
                return
            if await self._connect(address, reconnecting=True):
                return
        self._set_link(Link.DISCONNECTED, "Connection lost")

    async def _disconnect(self, user: bool) -> None:
        self._user_disconnect = user
        if self._connect_task and self._connect_task is not asyncio.current_task():
            self._connect_task.cancel()
        if self._client is None:
            if self.link != Link.DISCONNECTED:
                self._set_link(Link.DISCONNECTED, "")
            return
        self._set_link(Link.DISCONNECTING, "")
        await self._cleanup()
        self._set_link(Link.DISCONNECTED, "Disconnected")

    async def _cleanup(self) -> None:
        if self._writer:
            self._writer.cancel()
            self._writer = None
        if self._queue:
            self._queue.clear()
            self._queue = None
        client, self._client = self._client, None
        self.protocol = None
        if client is not None:
            try:
                await asyncio.wait_for(client.disconnect(), timeout=5)
            except Exception:  # noqa: BLE001
                pass
