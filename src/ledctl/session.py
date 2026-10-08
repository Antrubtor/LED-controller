"""Application logic: turns UI actions into controller commands."""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QTimer, Signal

from .audio.engine import MusicEngine
from .audio.visualizers import create as create_visualizer
from .ble.controller import BleController
from .config import Config
from .protocol import DeviceState, Protocol

log = logging.getLogger(__name__)


class Session(QObject):
    state_changed = Signal(object)  # DeviceState
    protocol_changed = Signal(object)  # Protocol | None

    def __init__(self, ble: BleController, engine: MusicEngine, config: Config) -> None:
        super().__init__()
        self.ble = ble
        self.engine = engine
        self.config = config
        self.protocol: Protocol | None = None
        self.state = DeviceState(brightness=config.brightness, rgb=config.color)
        self._feed_input: int | None = None  # audio input to restore after the PC audio music mode
        # Commands the controller must confirm: {field: [expected value, command, retries left]}.
        self._expect: dict[str, list] = {}
        self._verify_timer = QTimer(self, singleShot=True, interval=400)
        self._verify_timer.timeout.connect(self.ble.query_state)

        ble.connected.connect(self._on_connected)
        ble.link_changed.connect(self._on_link)
        ble.state_received.connect(self._on_state)
        engine.error.connect(self._on_engine_error)

    def _on_engine_error(self, _message: str) -> None:
        # The audio source could not be opened (or failed): give the controller its microphone back,
        # otherwise it keeps waiting for PC data and plays its demo animation.
        if not self.engine.running and self._feed_input is not None:
            if self.protocol:
                self._send_input(self._feed_input)
            self._feed_input = None

    # ------------------------------------------------------------------ events
    def _on_connected(self, protocol: Protocol, address: str) -> None:
        self.protocol = protocol
        self.engine.protocol = protocol
        self.config.last_address = address
        self.config.last_model = protocol.model.name
        self.config.save()
        self.protocol_changed.emit(protocol)

    def _on_link(self, link: str, _msg: str) -> None:
        if link in ("disconnected", "reconnecting") and self.protocol is not None:
            self.engine.stop()
            self.protocol = None
            self.engine.protocol = None
            self._expect.clear()
            self.protocol_changed.emit(None)

    def _send_verified(self, field: str, value: int, cmd: bytes) -> None:
        """Sends an important command, then reads the state back and resends it if it was ignored."""
        self.ble.send(cmd)
        self._expect[field] = [value, cmd, 2]
        self._verify_timer.start()

    def _send_effect(self, effect_id: int) -> None:
        if self.protocol:
            self._send_verified("effect", effect_id, self.protocol.cmd_effect(effect_id))

    def _send_input(self, value: int) -> None:
        if self.protocol:
            self._send_verified("audio_input", value, self.protocol.cmd_audio_input(value))

    def _check_expectations(self, st: DeviceState) -> None:
        resend = False
        for field, entry in list(self._expect.items()):
            value, cmd, retries = entry
            if getattr(st, field) == value or retries <= 0:
                if getattr(st, field) != value:
                    log.warning("The controller ignored %s=%s", field, value)
                del self._expect[field]
            else:
                log.debug("The controller ignored %s=%s, resending", field, value)
                entry[2] -= 1
                self.ble.send(cmd)
                resend = True
        if resend:
            self._verify_timer.start()

    def _on_state(self, st: DeviceState) -> None:
        if self._expect:
            self._check_expectations(st)
        # While the PC music mode runs, the reported brightness/color/speed belong to the animation: ignore them.
        if self.engine.running:
            st = st.copy(brightness=self.state.brightness, rgb=self.state.rgb, speed=self.state.speed)
        self.state = st
        self.state_changed.emit(st)

    def _update(self, **changes) -> None:
        self.state = self.state.copy(**changes)
        self.state_changed.emit(self.state)

    # ------------------------------------------------------------------ actions
    @property
    def connected(self) -> bool:
        return self.protocol is not None

    def _stop_music(self) -> None:
        if self.engine.running:
            self.engine.stop()
            self.ble.drop("music")
            self.ble.drop("music_speed")
            if self.protocol and self.state.brightness is not None:
                self.ble.send(self.protocol.cmd_brightness(self.state.brightness))
            if self.protocol and self.state.speed and self.engine.visualizer.speed is not None:
                self.ble.send(self.protocol.cmd_speed(self.state.speed))
        if self._feed_input is not None:
            # Give the controller its own audio input back, otherwise it waits for data and plays its demo.
            if self.protocol:
                self._send_input(self._feed_input)
                self.state = self.state.copy(audio_input=self._feed_input)
            self._feed_input = None

    def _ensure_on(self) -> None:
        if self.state.power is False and self.protocol:
            self.ble.send(self.protocol.cmd_power(True))
            self.state = self.state.copy(power=True)

    def power(self, on: bool) -> None:
        if not self.protocol:
            return
        if not on:
            self._stop_music()
        self.ble.send(self.protocol.cmd_power(on))
        self._update(power=on)

    def set_brightness(self, level: int) -> None:
        self.config.brightness = level
        self.engine.master_brightness = level
        self._update(brightness=level)
        if self.protocol and not self.engine.running:
            self.ble.send(self.protocol.cmd_brightness(level), key="brightness")

    def set_color(self, rgb: tuple[int, int, int], live: bool = True) -> None:
        """Solid color (or the color of the current effect when it accepts one)."""
        if not self.protocol:
            return
        self._stop_music()
        self._ensure_on()
        p = self.protocol
        current = p.effect(self.state.effect)
        if self.state.light_mode not in (None, 0):
            self.ble.send(p.cmd_light_mode(0))
        if current is None or not current.colorable:
            self._send_effect(p.effect_solid)
            self.state = self.state.copy(effect=p.effect_solid, light_mode=0)
        level = self.state.brightness if self.state.brightness is not None else 255
        self.ble.send(p.cmd_color(*rgb, level), key="color" if live else None)
        self.config.color = tuple(rgb)
        self._update(rgb=tuple(rgb), power=True)

    def set_white(self, level: int) -> None:
        p = self.protocol
        if not p or p.effect_white is None:
            return
        self._stop_music()
        self._ensure_on()
        if self.state.effect != p.effect_white:
            self._send_effect(p.effect_white)
        self.ble.send(p.cmd_white(level), key="white")
        self._update(effect=p.effect_white, white=level)

    def set_effect(self, effect_id: int) -> None:
        if not self.protocol:
            return
        self._stop_music()
        self._ensure_on()
        if self.state.light_mode not in (None, 0):
            self.ble.send(self.protocol.cmd_light_mode(0))
        self._send_effect(effect_id)
        self._update(effect=effect_id, light_mode=0, power=True)

    def set_light_mode(self, mode: int) -> None:
        if not self.protocol:
            return
        self._stop_music()
        self._ensure_on()
        self.ble.send(self.protocol.cmd_light_mode(mode))
        self._update(light_mode=mode)

    def set_speed(self, speed: int) -> None:
        if self.protocol:
            self.ble.send(self.protocol.cmd_speed(speed), key="speed")
            self._update(speed=speed)

    def set_length(self, length: int) -> None:
        if self.protocol and (cmd := self.protocol.cmd_length(length)):
            self.ble.send(cmd, key="length")
            self._update(length=length)

    def set_sensitivity(self, value: int) -> None:
        if self.protocol:
            self.ble.send(self.protocol.cmd_sensitivity(value), key="sensitivity")
            self._update(sensitivity=value)

    def set_audio_input(self, value: int) -> None:
        if self.protocol:
            self._send_input(value)
            self._update(audio_input=value)

    def set_chip_order(self, order: int) -> None:
        if self.protocol and (cmd := self.protocol.cmd_chip_order(order)):
            self.ble.send(cmd)
            self._update(chip_order=order)

    def refresh(self) -> None:
        self.ble.query_state()

    # ------------------------------------------------------------------ PC music
    def start_pc_music(self) -> None:
        if not self.protocol:
            return
        p = self.protocol
        vis = self.engine.visualizer
        if self.state.light_mode not in (None, 0):
            self.ble.send(p.cmd_light_mode(0))
        if vis.native_feed:
            if not p.supports_audio_feed:
                return
            effect = self.config.music_native_effect
            self._send_effect(effect)
            if self._feed_input is None:
                # Never "restore" the Player input: the controller would wait for data forever.
                self._feed_input = self.state.audio_input if self.state.audio_input not in (None, 1) else 0
            self._send_input(1)  # "Player": the controller now listens to our data
            self.state = self.state.copy(effect=effect, light_mode=0, audio_input=1)
        elif vis.brightness_only:
            effect = self.config.music_animated_effect
            self._send_effect(effect)
            self.state = self.state.copy(effect=effect, light_mode=0)
        elif self.state.effect != p.effect_solid:
            self._send_effect(p.effect_solid)
            self.state = self.state.copy(effect=p.effect_solid, light_mode=0)
        if self.state.power is False:
            self.ble.send(p.cmd_power(True))
        self.engine.master_brightness = self.state.brightness or 255
        self.engine.start()
        self._update(power=True)

    def stop_pc_music(self) -> None:
        self._stop_music()
        if self.protocol:
            p = self.protocol
            if self.engine.visualizer.brightness_only or self.engine.visualizer.native_feed:
                return  # the controller effect keeps running without the PC
            rgb = self.state.rgb or (255, 255, 255)
            self.ble.send(p.cmd_color(*rgb, self.state.brightness or 255))

    def set_animated_effect(self, effect_id: int) -> None:
        self.config.music_animated_effect = effect_id
        if self.engine.running and self.engine.visualizer.brightness_only and self.protocol:
            self._send_effect(effect_id)
            self.state = self.state.copy(effect=effect_id)

    def play_pc_effect(self, effect_id: int) -> None:
        """Runs a built-in music effect driven by the PC audio (the app's phone-microphone mode)."""
        if not self.protocol or not self.protocol.supports_audio_feed:
            return
        self.config.music_native_effect = effect_id
        if self.engine.running and self.engine.visualizer.native_feed:
            self.set_native_effect(effect_id)
            self._update(effect=effect_id)
            return
        self.switch_visualizer("native")
        if not self.engine.running:
            self.start_pc_music()

    def play_mic_effect(self, effect_id: int) -> None:
        """Runs a built-in music effect listening to the controller's own microphone."""
        if not self.protocol:
            return
        self._stop_music()  # also gives the controller its microphone back
        if self.state.audio_input == 1:
            self._send_input(0)
            self.state = self.state.copy(audio_input=0)
        self.set_effect(effect_id)

    def set_effect_color(self, rgb: tuple[int, int, int]) -> None:
        """Color of a single color effect, without interrupting a running music stream."""
        if not self.protocol:
            return
        level = self.state.brightness if self.state.brightness is not None else 255
        self.ble.send(self.protocol.cmd_color(*rgb, level), key="color")
        self._update(rgb=tuple(rgb))

    def set_native_effect(self, effect_id: int) -> None:
        self.config.music_native_effect = effect_id
        if self.engine.running and self.engine.visualizer.native_feed and self.protocol:
            self._send_effect(effect_id)
            self.state = self.state.copy(effect=effect_id)

    @staticmethod
    def _kind(vis) -> str:
        return "native" if vis.native_feed else "brightness" if vis.brightness_only else "color"

    def switch_visualizer(self, vis_id: str) -> None:
        was_running = self.engine.running
        old_kind = self._kind(self.engine.visualizer)
        if was_running and old_kind != self._kind_of(vis_id):
            self._stop_music()  # also hands the audio input back to the controller when leaving "native"
        self.engine.set_visualizer(vis_id)
        self.config.music_mode = vis_id
        if was_running and not self.engine.running:
            self.start_pc_music()

    def _kind_of(self, vis_id: str) -> str:
        return self._kind(create_visualizer(vis_id))
