"""Window, render loop and the dev-machine stand-in for the control surface.

The finished hardware has 24 pots, 3 crossfaders, 12 buttons and no screen. The
keyboard bindings here exist only so the engine is playable before that panel
exists; nothing else in the codebase depends on them.
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
from .midi.router import MidiRouter
from .sources.video import TestPattern, open_source

STATE_DIR = Path(__file__).resolve().parent.parent / "state"
PRESET_PATH = STATE_DIR / "preset.json"
BINDINGS_PATH = STATE_DIR / "bindings.json"


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
    def __init__(self, camera: bool = True, camera_index: int = 0,
                 audio_device=None, midi_port: str | None = None) -> None:
        self.bank, self.effects = build_patch()
        self.selected = 0  # index into bank.order, stands in for "which pot"

        self.audio = AudioAnalyzer(audio_device)
        self.audio.start()

        self.midi = MidiRouter(self.bank, midi_port)
        self.midi.start()
        if not self.midi.load(BINDINGS_PATH):
            self.midi.bindings = dict(DEFAULT_BINDINGS)
        self.midi.note_handler = self._on_note

        self.bank.load(PRESET_PATH)

        self.source = open_source(prefer_camera=camera, index=camera_index)
        self.mod_sources = [None, *AudioAnalyzer.source_names()]

        self._init_window()
        self.chain = Chain(self.ctx, self.effects, self.bank)

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
        """Note-on recalls a scene. Only slot 0 exists so far; the hardware
        spec calls for scene buttons, so the hook is wired now."""
        if note % 12 == 0 and self.bank.load(PRESET_PATH):
            print(f"  [scene] recalled preset via note {note}")

    def _on_key(self, window, key, scancode, action, mods) -> None:  # noqa: ARG002
        if action not in (glfw.PRESS, glfw.REPEAT):
            return
        p = self.selected_param
        step = 0.02 if mods & glfw.MOD_SHIFT else 0.05

        if key == glfw.KEY_ESCAPE:
            glfw.set_window_should_close(window, True)

        elif key in (glfw.KEY_1, glfw.KEY_2, glfw.KEY_3):
            idx = key - glfw.KEY_1
            if idx < len(self.effects):
                e = self.effects[idx]
                e.enabled = not e.enabled
                print(f"  {e.label}: {'ON' if e.enabled else 'BYPASS'}")

        elif key == glfw.KEY_TAB:
            delta = -1 if mods & glfw.MOD_SHIFT else 1
            self.selected = (self.selected + delta) % len(self.bank)
            self._print_selected()
        elif key in (glfw.KEY_RIGHT, glfw.KEY_UP):
            p.nudge(step)
            self._print_selected()
        elif key in (glfw.KEY_LEFT, glfw.KEY_DOWN):
            p.nudge(-step)
            self._print_selected()

        elif key == glfw.KEY_M:  # cycle this parameter's modulation source
            cur = self.mod_sources.index(p.mod_source) if p.mod_source in self.mod_sources else 0
            p.mod_source = self.mod_sources[(cur + 1) % len(self.mod_sources)]
            if p.mod_source and p.mod_depth == 0.0:
                p.mod_depth = 0.5  # a fresh source with zero depth does nothing
            self._print_selected()
        elif key == glfw.KEY_LEFT_BRACKET:
            p.mod_depth = max(-1.0, p.mod_depth - 0.05)
            self._print_selected()
        elif key == glfw.KEY_RIGHT_BRACKET:
            p.mod_depth = min(1.0, p.mod_depth + 0.05)
            self._print_selected()

        elif key == glfw.KEY_L:
            self.midi.arm_learn(p.key)
        elif key == glfw.KEY_K:
            self.midi.clear_binding(p.key)
            print(f"  [learn] cleared bindings for {p.key}")

        elif key == glfw.KEY_S:
            self.bank.save(PRESET_PATH)
            self.midi.save(BINDINGS_PATH)
            print(f"  saved preset + bindings to {STATE_DIR}")
        elif key == glfw.KEY_R:
            if self.bank.load(PRESET_PATH):
                print("  reloaded preset")
        elif key == glfw.KEY_T:
            self.source.close()
            self.source = TestPattern() if self.source.name != "test pattern" \
                else open_source(prefer_camera=True)
            print(f"  source: {self.source.name}")
        elif key == glfw.KEY_P:
            self.print_state()

    def _print_selected(self) -> None:
        p = self.selected_param
        value = p.resolve(self.audio.features)
        mod = f"  mod={p.mod_source}x{p.mod_depth:+.2f}" if p.mod_source else ""
        bound = [f"ch{c + 1}cc{n}" for (c, n), k in self.midi.bindings.items() if k == p.key]
        midi = f"  midi={','.join(bound)}" if bound else ""
        print(f"  [{self.selected:02d}] {p.key:<20} knob={p.base:.2f} -> {value:.3f}{mod}{midi}")

    def print_state(self) -> None:
        print("\n--- patch ---")
        for effect in self.effects:
            flag = "ON " if effect.enabled else "BYP"
            print(f"[{flag}] {effect.label}")
            for p in self.bank.by_group(effect.key):
                marker = ">" if self.bank.order[self.selected] == p.key else " "
                mod = f"  <- {p.mod_source} {p.mod_depth:+.2f}" if p.mod_source else ""
                print(f"   {marker} {p.label:<12} {p.base:.2f}{mod}")
        if self.midi.bpm:
            print(f"clock: {self.midi.bpm:.1f} BPM")
        print()

    # --- loop --------------------------------------------------------------

    def run(self) -> None:
        self.print_banner()
        screen = self.ctx.screen
        start = time.perf_counter()

        while not glfw.window_should_close(self.window):
            glfw.poll_events()
            now = time.perf_counter() - start

            self.chain.upload_source(self.source.read())
            features = self.audio.features
            values = self.bank.resolve_all(features)

            final = self.chain.render(values, features, now)
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
        print(f"  video: {self.source.name}")
        print("""
  keys   TAB select param      arrows adjust      1/2/3 bypass effect
         M   cycle mod source  [ ]    mod depth   L/K   MIDI learn / clear
         S   save preset       R      reload      T     toggle source
         P   print patch       ESC    quit
""")
        self.print_state()

    def shutdown(self) -> None:
        self.audio.stop()
        self.midi.stop()
        self.source.close()
        self.chain.release()
        glfw.terminate()
