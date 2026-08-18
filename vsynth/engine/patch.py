"""The default patch: which stages exist, in what order, with what defaults.

Declaration order is both the signal path and the intended panel layout, left
to right. The budget is 24 pots and 3 long-throw faders; `ParamBank.panel_counts`
reports what is actually spoken for, and the app prints it at startup so the
panel cannot quietly overrun the hardware.

The three faders are the blends worth a long throw: source A against source B,
the generative layer against the video, and the whole effects chain against
clean picture.
"""

from __future__ import annotations

from .effect import Effect, ParamSpec
from .params import ParamBank


def build_patch() -> tuple[ParamBank, list[Effect], Effect, Effect]:
    """Returns (bank, bypassable effects in order, mixer, master)."""
    bank = ParamBank()

    # --- input stage -------------------------------------------------------

    mixer = Effect(
        "mixer", "Mixer", "mixer.frag",
        [
            ParamSpec("xfade", "A / B", 0.0, 1.0, base=0.0, fader=True),
            ParamSpec("mode", "Blend Mode", 0.0, 4.0, base=0.0),
            ParamSpec("gain_b", "B Trim", 0.0, 2.0, base=0.5),
        ],
        bank,
    )

    # --- effects, in signal order -----------------------------------------

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

    kaleido = Effect(
        "kaleido", "Kaleidoscope", "kaleido.frag",
        [
            ParamSpec("segments", "Segments", 2.0, 16.0, base=0.3),
            ParamSpec("rotate", "Spin", -0.6, 0.6, base=0.5),  # centre = still
            ParamSpec("zoom", "Zoom", 0.4, 2.0, base=0.375),
            # Off by default: folding is a strong look and should be dialled
            # in, not something the instrument starts already doing.
            ParamSpec("mix", "Amount", 0.0, 1.0, base=0.0),
        ],
        bank,
    )

    generative = Effect(
        "generative", "Generative", "generative.frag",
        [
            ParamSpec("amount", "Layer", 0.0, 1.0, base=0.0, fader=True),
            ParamSpec("scale", "Scale", 1.0, 12.0, base=0.3),
            ParamSpec("speed", "Speed", 0.0, 1.0, base=0.3),
            ParamSpec("warp", "Warp", 0.0, 4.0, base=0.4),
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

    # --- output stage ------------------------------------------------------

    master = Effect(
        "master", "Master", "master.frag",
        [
            ParamSpec("wet", "Dry / Wet", 0.0, 1.0, base=1.0, fader=True),
            ParamSpec("level", "Level", 0.0, 1.5, base=0.667),
            # Scales every modulation depth at once. With no screen to check
            # routings on, one knob for "how reactive is this right now" is the
            # control most worth reaching for mid-set.
            ParamSpec("audio", "Audio Depth", 0.0, 2.0, base=0.5),
        ],
        bank,
    )

    # Generative sits before feedback so generated content accumulates in the
    # feedback loop instead of washing flatly over the result.
    effects = [glitch, kaleido, generative, feedback, colour]
    return bank, effects, mixer, master


# Shipped so a fresh install responds to a controller without having to
# MIDI-learn first. (channel, cc) -> param key.
DEFAULT_BINDINGS = {
    (0, 1): "mixer.xfade",       # mod wheel -- the fader you reach for most
    (0, 74): "glitch.amount",    # filter cutoff on most controllers
    (0, 71): "color.hue",
    (0, 7): "master.wet",        # channel volume
}
