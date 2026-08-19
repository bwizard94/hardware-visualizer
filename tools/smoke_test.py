"""Headless check of the render path.

Compiles every shader and verifies each stage actually changes the picture --
a shader that compiles but is wired to nothing looks identical to one that
works, and only the second check catches that. Run this before opening a
window; a GLSL error reads far better here than as a black screen.
"""

from __future__ import annotations

import sys
from pathlib import Path

import moderngl
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vsynth.config import CANVAS_H, CANVAS_W
from vsynth.engine.chain import Chain
from vsynth.engine.patch import build_patch
from vsynth.sources.video import TestPattern

PANEL_POTS, PANEL_FADERS = 24, 3

# Non-zero audio, so modulated parameters are exercised rather than sitting at
# their unmodulated defaults.
FEATURES = {k: 0.5 for k in
            ["mix.bass", "mix.lowmid", "mix.mid", "mix.high", "mix.hit", "l.hit", "r.hit"]}

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}{('  -- ' + detail) if detail else ''}")
    if not ok:
        failures.append(name)


class Rig:
    def __init__(self) -> None:
        self.ctx = moderngl.create_standalone_context(require=330)
        self.bank, self.effects, self.mixer, self.master = build_patch()
        self.chain = Chain(self.ctx, self.effects, self.mixer, self.master, self.bank)
        self.a = TestPattern("bars")
        self.b = TestPattern("grid")

    def frame(self, frames: int = 4, beats: float | None = None, **knobs) -> np.ndarray:
        """Render with the given knob positions and read the result back.

        `beats` pins the musical clock to a fixed position, so clock-locked
        behaviour is reproducible instead of depending on wall time.
        """
        restore = {k: self.bank.get(k).base for k in knobs}
        for key, value in knobs.items():
            self.bank.get(key).base = value

        features = dict(FEATURES)
        clock = (0.0, 0.0, 0.0, 0.0)
        if beats is not None:
            phase = beats % 1.0
            clock = (phase, (beats / 4.0) % 1.0, (1.0 - phase) ** 3, beats)
            features.update({
                "clk.beat": phase, "clk.bar": (beats / 4.0) % 1.0,
                "clk.8th": (beats * 2) % 1.0, "clk.16th": (beats * 4) % 1.0,
                "clk.pulse": (1.0 - phase) ** 3,
                "clk.tri": 1.0 - abs(phase * 2.0 - 1.0),
            })

        for i in range(frames):
            self.chain.upload_sources(self.a.read(), self.b.read())
            final = self.chain.render(self.bank.resolve_all(features), features,
                                      i / 60.0, clock)

        fbo = self.ctx.framebuffer(color_attachments=[final])
        img = np.frombuffer(fbo.read(components=3, dtype="f1"), dtype=np.uint8)

        for key, value in restore.items():
            self.bank.get(key).base = value
        return img.reshape(CANVAS_H, CANVAS_W, 3)

    def freeze_sources(self) -> None:
        """Pin both sources to a single frame.

        The test patterns animate on every read, so anything comparing two
        renders would otherwise be measuring the moving sweep line rather than
        the thing under test.
        """
        class _Frozen:
            name = "frozen"

            def __init__(self, frame):
                self._frame = frame

            def read(self):
                return self._frame

            def close(self):
                pass

        self.a = _Frozen(self.a.read())
        self.b = _Frozen(self.b.read())

    def only(self, key: str | None) -> None:
        """Bypass every effect except one."""
        for effect in self.effects:
            effect.enabled = effect.key == key


def diff(x: np.ndarray, y: np.ndarray) -> float:
    return float(np.abs(x.astype(int) - y.astype(int)).mean())


def main() -> int:
    rig = Rig()
    print(f"GL {rig.ctx.info['GL_VERSION']} / {rig.ctx.info['GL_RENDERER']}")

    pots, faders = rig.bank.panel_counts()
    print(f"patch: {len(rig.effects)} effects, {len(rig.bank)} parameters "
          f"({pots} pots + {faders} faders)\nall shaders compiled\n")

    check("panel budget", pots <= PANEL_POTS and faders <= PANEL_FADERS,
          f"{pots}/{PANEL_POTS} pots, {faders}/{PANEL_FADERS} faders")

    # --- baseline ----------------------------------------------------------
    rig.only(None)
    clean = rig.frame()
    check("renders a picture", clean.max() > 0 and len(np.unique(clean)) > 8,
          f"mean={clean.mean():.1f}, {len(np.unique(clean))} levels")

    # --- mixer -------------------------------------------------------------
    # The crossfader is the whole point of two inputs: the ends must be the two
    # sources, and the middle must be neither.
    a_only = rig.frame(**{"mixer.xfade": 0.0})
    b_only = rig.frame(**{"mixer.xfade": 1.0})
    middle = rig.frame(**{"mixer.xfade": 0.5})
    check("crossfade reaches both sources", diff(a_only, b_only) > 20.0,
          f"A vs B = {diff(a_only, b_only):.1f}")
    check("crossfade blends in between",
          diff(middle, a_only) > 5.0 and diff(middle, b_only) > 5.0,
          f"mid vs A = {diff(middle, a_only):.1f}, vs B = {diff(middle, b_only):.1f}")

    for mode, name in enumerate(["mix", "add", "difference", "multiply", "key"]):
        out = rig.frame(**{"mixer.xfade": 1.0, "mixer.mode": mode / 4.0})
        check(f"blend mode {name}", out.max() > 0)

    # --- each effect, in isolation -----------------------------------------
    # Settings that should visibly bite, so "no change" means broken wiring.
    probes = {
        "glitch": {"glitch.amount": 0.9, "glitch.shift": 0.6},
        "kaleido": {"kaleido.mix": 1.0, "kaleido.segments": 0.5},
        "generative": {"generative.amount": 1.0, "generative.warp": 0.6},
        "feedback": {"feedback.mix": 0.9, "feedback.zoom": 0.7},
        "color": {"color.hue": 0.4, "color.posterize": 0.05},
    }
    for effect in rig.effects:
        rig.only(effect.key)
        # Feedback needs history to build before it looks like anything.
        out = rig.frame(frames=24 if effect.key == "feedback" else 4, **probes[effect.key])
        check(f"{effect.key} changes the picture", diff(out, clean) > 2.0,
              f"delta={diff(out, clean):.1f}")

    # --- master ------------------------------------------------------------
    rig.only("kaleido")
    wet = rig.frame(**{"kaleido.mix": 1.0, "kaleido.segments": 0.6, "master.wet": 1.0})
    dry = rig.frame(**{"kaleido.mix": 1.0, "kaleido.segments": 0.6, "master.wet": 0.0})
    check("dry/wet fader bypasses the chain", diff(dry, clean) < 1.0,
          f"dry vs clean = {diff(dry, clean):.2f}")
    check("dry/wet fader reaches full wet", diff(wet, dry) > 5.0,
          f"wet vs dry = {diff(wet, dry):.1f}")

    rig.only(None)
    dark = rig.frame(**{"master.level": 0.0})
    check("master level closes down", dark.max() == 0, f"max={dark.max()}")

    print("\nclock sync")
    rig.freeze_sources()
    rig.only("glitch")
    glitch = dict(frames=1, **{"glitch.amount": 0.8, "glitch.blocks": 0.5})
    # Tearing must hold still within a sixteenth and re-roll across one --
    # that is the whole point of driving the step index off the clock.
    within_a = rig.frame(beats=0.00, **glitch)
    within_b = rig.frame(beats=0.20, **glitch)   # same 16th (0.00-0.25)
    across = rig.frame(beats=0.30, **glitch)     # next 16th
    check("glitch holds within a sixteenth", diff(within_a, within_b) < 1.0,
          f"delta={diff(within_a, within_b):.2f}")
    check("glitch re-rolls on the next sixteenth", diff(within_a, across) > 5.0,
          f"delta={diff(within_a, across):.2f}")

    # Deterministic for a given position -- but deliberately not periodic per
    # bar. The step index counts up without wrapping, so every sixteenth gets
    # fresh tearing; a pattern that repeated each bar would read as mechanical.
    check("same clock position is deterministic",
          diff(rig.frame(beats=0.30, **glitch), across) < 0.01,
          f"delta={diff(rig.frame(beats=0.30, **glitch), across):.4f}")
    check("tearing does not repeat every bar",
          diff(rig.frame(beats=4.30, **glitch), across) > 5.0,
          f"delta={diff(rig.frame(beats=4.30, **glitch), across):.2f}")

    # Clock modulation has to reach a parameter, not just the shader uniform.
    rig.only("color")
    rig.bank.get("color.hue").mod_source = "clk.pulse"
    rig.bank.get("color.hue").mod_depth = 0.8
    on_beat = rig.frame(beats=0.0, frames=1)
    off_beat = rig.frame(beats=0.5, frames=1)
    check("clock modulation drives a parameter", diff(on_beat, off_beat) > 5.0,
          f"delta={diff(on_beat, off_beat):.2f}")
    rig.bank.get("color.hue").mod_source = None
    rig.bank.get("color.hue").mod_depth = 0.0

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s) -- {', '.join(failures)}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
