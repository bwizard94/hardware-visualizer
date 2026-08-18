"""The default patch: which effects exist, in what order, with what defaults.

Ordering here is the signal path -- glitch mangles the source, feedback builds
on the mangled result, colour grades whatever comes out. Parameter declaration
order is also the intended panel order, four controls per effect, matching the
24-pot layout.
"""

from __future__ import annotations

from .effect import Effect, ParamSpec
from .params import ParamBank


def build_patch() -> tuple[ParamBank, list[Effect]]:
    bank = ParamBank()

    glitch = Effect(
        "glitch", "Glitch", "glitch.frag",
        [
            # Transients drive tearing by default -- it is the mapping that
            # makes the instrument feel audio-reactive the moment it starts.
            ParamSpec("amount", "Amount", 0.0, 1.0, base=0.15,
                      mod_source="mix.hit", mod_depth=0.55),
            ParamSpec("blocks", "Blocks", 4.0, 80.0, base=0.35),
            ParamSpec("shift", "RGB Shift", 0.0, 0.05, base=0.2,
                      mod_source="mix.high", mod_depth=0.4),
            ParamSpec("scan", "Scanlines", 0.0, 1.0, base=0.25),
        ],
        bank,
    )

    feedback = Effect(
        "feedback", "Feedback", "feedback.frag",
        [
            ParamSpec("mix", "Amount", 0.0, 0.98, base=0.0,
                      mod_source="mix.bass", mod_depth=0.5),
            # Zoom sits just above 1.0 at rest so feedback pushes inward; an
            # exact 1.0 freezes the tunnel and looks like a plain smear.
            ParamSpec("zoom", "Zoom", 0.90, 1.10, base=0.62),
            ParamSpec("rotate", "Rotate", -0.05, 0.05, base=0.5),
            ParamSpec("decay", "Decay", 0.70, 1.0, base=0.75),
        ],
        bank,
    )

    colour = Effect(
        "color", "Colour", "color.frag",
        [
            ParamSpec("hue", "Hue", 0.0, 1.0, base=0.0),
            ParamSpec("sat", "Saturation", 0.0, 3.0, base=0.333),
            ParamSpec("gain", "Gain", 0.0, 2.0, base=0.5),
            ParamSpec("posterize", "Posterize", 2.0, 64.0, base=1.0),
        ],
        bank,
    )

    return bank, [glitch, feedback, colour]


# Shipped so a fresh install has something responding to a controller without
# having to MIDI-learn first. (channel, cc) -> param key.
DEFAULT_BINDINGS = {
    (0, 1): "feedback.mix",     # mod wheel
    (0, 74): "glitch.amount",   # filter cutoff on most controllers
    (0, 71): "color.hue",
}
