"""MIDI input, CC routing and MIDI learn.

The finished panel has DIN In/Out/Thru plus USB host; on the dev machine both
arrive as ordinary mido ports, so nothing here needs to know which is which.

CC values move a parameter's knob position directly -- a CC and a pot are the
same gesture, so they write to the same place rather than layering on top of
each other.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import mido

from .clock import MidiClock


class MidiRouter:
    def __init__(self, bank, port_name: str | None = None) -> None:
        self.bank = bank
        self.port_name = port_name
        self.port: mido.ports.BaseInput | None = None
        self.error: str | None = None

        # (channel, cc) -> param key. Channel is kept in the key so two
        # controllers can send the same CC without colliding.
        self.bindings: dict[tuple[int, int], str] = {}
        self.learn_target: str | None = None
        self.last_cc: tuple[int, int] | None = None

        # Transport and tempo live in MidiClock, which also free-runs when no
        # external clock is present.
        self.clock = MidiClock()

        self.note_handler = None  # set by the app for scene recall

    # --- port management ---------------------------------------------------

    @staticmethod
    def available_ports() -> list[str]:
        try:
            return mido.get_input_names()
        except Exception:
            return []

    def start(self) -> bool:
        names = self.available_ports()
        if not names:
            self.error = "no MIDI inputs found"
            return False

        target = names[0]
        if self.port_name:
            matches = [n for n in names if self.port_name.lower() in n.lower()]
            if not matches:
                self.error = f"no port matching {self.port_name!r} (have: {names})"
                return False
            target = matches[0]

        try:
            self.port = mido.open_input(target, callback=self._on_message)
            return True
        except Exception as exc:
            self.error = str(exc)
            return False

    def stop(self) -> None:
        if self.port is not None:
            self.port.close()
            self.port = None

    def describe(self) -> str:
        if self.port is None:
            return f"midi:  OFF ({self.error or 'no port'})"
        return f"midi:  {self.port.name} ({len(self.bindings)} binding(s))"

    # --- message handling --------------------------------------------------

    def _on_message(self, msg: mido.Message) -> None:
        # Realtime messages are timestamped on arrival: mido delivers them on
        # its own thread, and the render thread interpolates phase from these
        # timestamps rather than from when it happened to look.
        if msg.type == "control_change":
            self._on_cc(msg.channel, msg.control, msg.value)
        elif msg.type == "note_on" and msg.velocity > 0:
            if self.note_handler:
                self.note_handler(msg.note)
        elif msg.type == "clock":
            self.clock.on_tick(time.perf_counter())
        elif msg.type == "start":
            self.clock.on_start(time.perf_counter())
            print("  [clock] start")
        elif msg.type == "continue":
            self.clock.on_continue(time.perf_counter())
        elif msg.type == "stop":
            self.clock.on_stop(time.perf_counter())
            print("  [clock] stop")
        elif msg.type == "songpos":
            self.clock.on_song_position(msg.pos)

    def _on_cc(self, channel: int, cc: int, value: int) -> None:
        addr = (channel, cc)
        self.last_cc = addr

        if self.learn_target is not None:
            # Clear any previous binding for this parameter so one control
            # never ends up driven by two CCs at once.
            for existing in [a for a, k in self.bindings.items() if k == self.learn_target]:
                del self.bindings[existing]
            self.bindings[addr] = self.learn_target
            print(f"  [learn] ch{channel + 1} cc{cc} -> {self.learn_target}")
            self.learn_target = None
            return

        key = self.bindings.get(addr)
        if key and key in self.bank:
            self.bank.get(key).base = value / 127.0

    # --- learn / persistence -----------------------------------------------

    def arm_learn(self, param_key: str) -> None:
        self.learn_target = param_key
        print(f"  [learn] armed for {param_key} -- move a control")

    def cancel_learn(self) -> None:
        self.learn_target = None

    def clear_binding(self, param_key: str) -> None:
        for addr in [a for a, k in self.bindings.items() if k == param_key]:
            del self.bindings[addr]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {f"{ch}:{cc}": key for (ch, cc), key in self.bindings.items()}
        path.write_text(json.dumps(data, indent=2))

    def load(self, path: Path) -> bool:
        if not path.exists():
            return False
        data = json.loads(path.read_text())
        self.bindings = {}
        for addr, key in data.items():
            ch, cc = addr.split(":")
            self.bindings[(int(ch), int(cc))] = key
        return True
