"""Window, render loop and the dev-machine stand-in for the control surface.

The finished hardware has 24 pots, 3 long-throw faders, 12 illuminated buttons
and no screen. The keyboard bindings here exist only so the engine is playable
before that panel exists; nothing else in the codebase depends on them.
"""

from __future__ import annotations

import time
from pathlib import Path

import glfw
import moderngl

from .audio.analyzer import AudioAnalyzer
from .config import CANVAS_H, CANVAS_W, WINDOW_H, WINDOW_W
from .engine.chain import Chain
from .engine.patch import DEFAULT_BINDINGS, build_patch
from .engine.scenes import RECALL_NOTE_BASE, STORE_NOTE_BASE, SceneBank
from .midi.clock import MidiClock
from .midi.router import MidiRouter
from .sources.video import open_spec

STATE_DIR = Path(__file__).resolve().parent.parent / "state"
SCENES_PATH = STATE_DIR / "scenes.json"
BINDINGS_PATH = STATE_DIR / "bindings.json"

PANEL_POTS = 24
PANEL_FADERS = 3
# Twelve illuminated buttons: five effect bypasses, tap tempo, and six scenes.
PANEL_BUTTONS = 12


def fit_viewport(fb_w: int, fb_h: int) -> tuple[int, int, int, int]:
    """Letterbox the canvas into the window without distorting it."""
    target = CANVAS_W / CANVAS_H
    if fb_w / max(fb_h, 1) > target:
        h = fb_h
        w = int(h * target)
    else:
        w = fb_w
        h = int(w / target)
    return ((fb_w - w) // 2, (fb_h - h) // 2, w, h)


class App:
    def __init__(self, source_a: str = "cam:0", source_b: str = "grid",
                 audio_device=None, midi_port: str | None = None) -> None:
        self.bank, self.effects, self.mixer, self.master = build_patch()
        self.selected = 0  # index into bank.order, stands in for "which control"

        self.audio = AudioAnalyzer(audio_device)
        self.audio.start()

        self.midi = MidiRouter(self.bank, midi_port)
        self.midi.start()
        if not self.midi.load(BINDINGS_PATH):
            self.midi.bindings = dict(DEFAULT_BINDINGS)
        self.midi.note_handler = self._on_note

        self.scenes = SceneBank(self.bank, self.effects, SCENES_PATH)
        self.scenes.load()
        # Assigned only once the scene bank exists -- MIDI arrives on its own
        # thread and could otherwise fire before this line.
        self.midi.on_control_moved = lambda key: self.scenes.touch()
        # Power on into scene 1 if one was stored, the way a hardware
        # instrument comes up in its last committed state.
        self.scenes.recall(0)

        self.source_a = open_spec(source_a)
        self.source_b = open_spec(source_b)
        self.mod_sources = [None, *AudioAnalyzer.source_names(), *MidiClock.source_names()]

        self._init_window()
        self.chain = Chain(self.ctx, self.effects, self.mixer, self.master, self.bank)

        self.frames = 0
        self.fps = 0.0
        self._fps_mark = time.perf_counter()

    # --- window ------------------------------------------------------------

    def _init_window(self) -> None:
        if not glfw.init():
            raise RuntimeError("glfw failed to initialise")

        # 3.3 core + forward compat is the highest macOS will hand out, and it
        # is also the floor the shaders are written against.
        glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 3)
        glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 3)
        glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)
        glfw.window_hint(glfw.OPENGL_FORWARD_COMPAT, glfw.TRUE)

        self.window = glfw.create_window(WINDOW_W, WINDOW_H, "vsynth", None, None)
        if not self.window:
            glfw.terminate()
            raise RuntimeError("could not create window")

        glfw.make_context_current(self.window)
        glfw.swap_interval(1)  # vsync; the hardware will be genlocked instead
        glfw.set_key_callback(self.window, self._on_key)
        self.ctx = moderngl.create_context()

    # --- input -------------------------------------------------------------

    @property
    def selected_param(self):
        return self.bank.get(self.bank.order[self.selected])

    def _on_note(self, note: int) -> None:
        """Scene buttons over MIDI: recall from C3 up, store an octave above."""
        if RECALL_NOTE_BASE <= note < RECALL_NOTE_BASE + self.scenes.slots:
            self._recall_scene(note - RECALL_NOTE_BASE)
        elif STORE_NOTE_BASE <= note < STORE_NOTE_BASE + self.scenes.slots:
            self._store_scene(note - STORE_NOTE_BASE)

    def _recall_scene(self, slot: int) -> None:
        if not self.scenes.recall(slot):
            print(f"  [scene] {slot + 1} is empty")
            return
        # Every physical control is now at the wrong position for the values
        # just loaded, so drop them all out of pickup.
        self.midi.invalidate_pickup()
        waiting = self.midi.waiting_count()
        note = f"  ({waiting} control(s) waiting for pickup)" if waiting else ""
        print(f"  [scene] recalled {slot + 1}{note}")

    def _store_scene(self, slot: int) -> None:
        self.scenes.store(slot)
        print(f"  [scene] stored {slot + 1} -> {SCENES_PATH.name}")

    def _on_key(self, window, key, scancode, action, mods) -> None:  # noqa: ARG002
        if action not in (glfw.PRESS, glfw.REPEAT):
            return
        p = self.selected_param
        step = 0.02 if mods & glfw.MOD_SHIFT else 0.05

        if key == glfw.KEY_ESCAPE:
            glfw.set_window_should_close(window, True)

        elif glfw.KEY_1 <= key <= glfw.KEY_9:
            idx = key - glfw.KEY_1
            if idx < len(self.effects):
                e = self.effects[idx]
                e.enabled = not e.enabled
                self.scenes.touch()
                print(f"  {e.label}: {'ON' if e.enabled else 'BYPASS'}")

        elif key == glfw.KEY_TAB:
            delta = -1 if mods & glfw.MOD_SHIFT else 1
            self.selected = (self.selected + delta) % len(self.bank)
            self._print_selected()
        elif key in (glfw.KEY_RIGHT, glfw.KEY_UP):
            p.nudge(step)
            self.scenes.touch()
            self._print_selected()
        elif key in (glfw.KEY_LEFT, glfw.KEY_DOWN):
            p.nudge(-step)
            self.scenes.touch()
            self._print_selected()

        elif key == glfw.KEY_M:  # cycle this parameter's modulation source
            cur = self.mod_sources.index(p.mod_source) if p.mod_source in self.mod_sources else 0
            p.mod_source = self.mod_sources[(cur + 1) % len(self.mod_sources)]
            if p.mod_source and p.mod_depth == 0.0:
                p.mod_depth = 0.5  # a fresh source with zero depth does nothing
            self.scenes.touch()
            self._print_selected()
        elif key == glfw.KEY_LEFT_BRACKET:
            p.mod_depth = max(-1.0, p.mod_depth - 0.05)
            self.scenes.touch()
            self._print_selected()
        elif key == glfw.KEY_RIGHT_BRACKET:
            p.mod_depth = min(1.0, p.mod_depth + 0.05)
            self.scenes.touch()
            self._print_selected()

        elif key == glfw.KEY_L:
            self.midi.arm_learn(p.key)
        elif key == glfw.KEY_K:
            self.midi.clear_binding(p.key)
            print(f"  [learn] cleared bindings for {p.key}")

        elif glfw.KEY_F1 <= key <= glfw.KEY_F1 + self.scenes.slots - 1:
            slot = key - glfw.KEY_F1
            if mods & glfw.MOD_SHIFT:
                self._store_scene(slot)
            else:
                self._recall_scene(slot)
        elif key == glfw.KEY_R:
            if self.scenes.revert():
                self.midi.invalidate_pickup()
                print(f"  [scene] reverted to {self.scenes.active + 1}")
        elif key == glfw.KEY_T:
            self.midi.pickup = not self.midi.pickup
            print(f"  pickup (soft takeover): {'ON' if self.midi.pickup else 'OFF'}")

        elif key == glfw.KEY_S:
            self.midi.save(BINDINGS_PATH)
            print(f"  saved MIDI bindings to {BINDINGS_PATH.name}"
                  "  (scenes save themselves when stored)")
        elif key == glfw.KEY_X:
            self.source_a, self.source_b = self.source_b, self.source_a
            print(f"  swapped: A={self.source_a.name}  B={self.source_b.name}")
        elif key == glfw.KEY_B:
            bpm = self.midi.clock.tap(time.perf_counter())
            if bpm:
                print(f"  [clock] tapped {bpm:.1f} BPM")
            else:
                print("  [clock] tap...")
        elif key == glfw.KEY_N:
            self.midi.clock.reset_phase(time.perf_counter())
            print("  [clock] downbeat")

        elif key == glfw.KEY_P:
            self.print_state()

    def _print_selected(self) -> None:
        p = self.selected_param
        value = p.resolve(self.audio.features)
        kind = "fader" if p.fader else "knob "
        mod = f"  mod={p.mod_source}x{p.mod_depth:+.2f}" if p.mod_source else ""
        bound = [f"ch{c + 1}cc{n}" for (c, n), k in self.midi.bindings.items() if k == p.key]
        midi = f"  midi={','.join(bound)}" if bound else ""
        pickup = self.midi.pickup_status(p.key)
        wait = f"  [{pickup} to pick up]" if pickup else ""
        print(f"  [{self.selected:02d}] {p.key:<20} {kind}={p.base:.2f} -> "
              f"{value:.3f}{mod}{midi}{wait}")

    def print_state(self) -> None:
        print("\n--- patch ---")
        stages = [(self.mixer, None)]
        stages += [(e, e) for e in self.effects]
        stages += [(self.master, None)]

        for stage, bypassable in stages:
            if bypassable is None:
                flag = "---"
            else:
                flag = "ON " if stage.enabled else "BYP"
            print(f"[{flag}] {stage.label}")
            for p in self.bank.by_group(stage.key):
                marker = ">" if self.bank.order[self.selected] == p.key else " "
                kind = "|" if p.fader else " "  # faders stand out at a glance
                mod = f"  <- {p.mod_source} {p.mod_depth:+.2f}" if p.mod_source else ""
                print(f"   {marker}{kind} {p.label:<12} {p.base:.2f}{mod}")

        pots, faders = self.bank.panel_counts()
        print(f"panel: {pots}/{PANEL_POTS} pots, {faders}/{PANEL_FADERS} faders")
        lamps = {"off": ".", "dim": "o", "on": "*", "blink": "!"}
        row = "  ".join(
            f"{i + 1}{lamps[self.scenes.led(i)]}" for i in range(self.scenes.slots)
        )
        active = "none" if self.scenes.active is None else str(self.scenes.active + 1)
        edited = " (edited)" if self.scenes.dirty else ""
        print(f"scenes: {row}     active: {active}{edited}")
        print("        . empty   o stored   * active   ! active but edited")

        waiting = self.midi.waiting_count()
        if self.midi.pickup and waiting:
            print(f"pickup: {waiting} bound control(s) waiting to be picked up")

        now = time.perf_counter()
        clock = self.midi.clock
        print(f"clock: {clock.bpm(now):.1f} BPM  [{clock.source(now)}]")
        print()

    # --- loop --------------------------------------------------------------

    def run(self) -> None:
        self.print_banner()
        screen = self.ctx.screen
        start = time.perf_counter()

        while not glfw.window_should_close(self.window):
            glfw.poll_events()
            now = time.perf_counter() - start

            self.chain.upload_sources(self.source_a.read(), self.source_b.read())

            # One knob scales every modulation depth at once. It is resolved
            # first and applied to the features, so it reaches both the
            # modulation matrix and the audio uniforms the shaders read.
            features = self.audio.features
            depth = self.bank.get("master.audio").resolve(features)
            if depth != 1.0:
                features = {k: v * depth for k, v in features.items()}

            # Clock sources merge in *after* that scaling. "Audio Depth" means
            # how hard the visuals react to sound; scaling the beat phase by it
            # would make tempo-locked motion stall whenever that knob came
            # down, which is not what anyone reaching for it intends.
            clock = self.midi.clock
            wall = time.perf_counter()
            features = {**features, **clock.features(wall)}

            values = self.bank.resolve_all(features)
            final = self.chain.render(values, features, now, clock.shader_clock(wall))

            fb_w, fb_h = glfw.get_framebuffer_size(self.window)
            self.chain.to_screen(final, screen, fit_viewport(fb_w, fb_h))

            glfw.swap_buffers(self.window)
            self._tick_fps()

        self.shutdown()

    def _tick_fps(self) -> None:
        self.frames += 1
        if self.frames % 60 == 0:
            now = time.perf_counter()
            self.fps = 60.0 / (now - self._fps_mark)
            self._fps_mark = now
            glfw.set_window_title(self.window, f"vsynth  {self.fps:.0f} fps")

    def print_banner(self) -> None:
        print(f"\nvsynth -- {CANVAS_W}x{CANVAS_H} canvas, {len(self.bank)} parameters")
        print(f"  {self.audio.describe()}")
        print(f"  {self.midi.describe()}")
        print(f"  video: A={self.source_a.name}  B={self.source_b.name}")
        print("""
  keys   TAB select control     arrows adjust      1-5 bypass effect
         M   cycle mod source   [ ]    mod depth   L/K MIDI learn / clear
         X   swap A and B
         B   tap tempo          N      downbeat    P   print patch
         F1-F6 recall scene       shift+F1-F6 store scene
         R   revert scene         T      pickup on/off       S save bindings
         ESC quit
""")
        self.print_state()

    def shutdown(self) -> None:
        self.audio.stop()
        self.midi.stop()
        self.source_a.close()
        self.source_b.close()
        self.chain.release()
        glfw.terminate()
