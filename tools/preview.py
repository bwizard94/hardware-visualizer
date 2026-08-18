"""Render still frames of several looks, and time the chain.

The timing number matters beyond this prototype: it is the first real evidence
of how much GPU work the effects chain actually costs at 720p, which is the
open question behind the compute-core choice.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import cv2
import moderngl
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vsynth.config import CANVAS_H, CANVAS_W
from vsynth.engine.chain import Chain
from vsynth.engine.patch import build_patch
from vsynth.sources.video import TestPattern

OUT = Path(__file__).resolve().parent.parent / "previews"

# name -> (bypassed effects, knob overrides). Each is a plausible performance
# patch, not just a parameter sweep.
LOOKS = {
    "01_clean":    (["glitch", "feedback", "color"], {}),
    "02_glitch":   (["feedback"], {"glitch.amount": 0.75, "glitch.blocks": 0.45,
                                   "glitch.shift": 0.5, "glitch.scan": 0.4}),
    "03_feedback": (["glitch"], {"feedback.mix": 0.85, "feedback.zoom": 0.70,
                                 "feedback.rotate": 0.62, "feedback.decay": 0.90,
                                 "color.sat": 0.6}),
    "04_full":     ([], {"glitch.amount": 0.45, "glitch.shift": 0.4,
                         "feedback.mix": 0.7, "feedback.zoom": 0.66,
                         "feedback.rotate": 0.56, "feedback.decay": 0.85,
                         "color.hue": 0.15, "color.sat": 0.55, "color.posterize": 0.12}),
}

# Mid-level audio, so modulated parameters sit where they would during a track.
FEATURES = {
    "mix.bass": 0.6, "mix.lowmid": 0.4, "mix.mid": 0.35, "mix.high": 0.5,
    "mix.hit": 0.7, "l.hit": 0.7, "r.hit": 0.6,
}


def read_rgb(ctx, tex) -> np.ndarray:
    fbo = ctx.framebuffer(color_attachments=[tex])
    buf = fbo.read(components=3, dtype="f1")
    return np.frombuffer(buf, dtype=np.uint8).reshape(CANVAS_H, CANVAS_W, 3)


def main() -> int:
    OUT.mkdir(exist_ok=True)
    ctx = moderngl.create_standalone_context(require=330)
    bank, effects = build_patch()
    chain = Chain(ctx, effects, bank)
    source = TestPattern()
    tiles = []

    for name, (bypass, overrides) in LOOKS.items():
        defaults = {p.key: p.base for p in bank}
        for effect in effects:
            effect.enabled = effect.key not in bypass
        for key, value in overrides.items():
            bank.get(key).base = value

        # Feedback needs history to accumulate before it looks like anything.
        for frame in range(60):
            chain.upload_source(source.read())
            final = chain.render(bank.resolve_all(FEATURES), FEATURES, frame / 60.0)

        img = read_rgb(ctx, final)
        cv2.imwrite(str(OUT / f"{name}.png"), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
        label = img.copy()
        cv2.putText(label, name[3:], (24, 64), cv2.FONT_HERSHEY_SIMPLEX,
                    1.6, (255, 255, 255), 4, cv2.LINE_AA)
        tiles.append(cv2.resize(label, (CANVAS_W // 2, CANVAS_H // 2)))
        print(f"  {name}: mean={img.mean():.1f}")

        for p in bank:
            p.base = defaults[p.key]

    sheet = np.vstack([np.hstack(tiles[:2]), np.hstack(tiles[2:])])
    cv2.imwrite(str(OUT / "contact_sheet.png"), cv2.cvtColor(sheet, cv2.COLOR_RGB2BGR))

    # Timing: all effects on, GPU flushed either side so the number is real
    # per-frame cost and not just command submission.
    for effect in effects:
        effect.enabled = True
    for _ in range(20):  # warm up shader and pipeline caches
        chain.upload_source(source.read())
        chain.render(bank.resolve_all(FEATURES), FEATURES, 0.0)
    ctx.finish()

    n = 200
    start = time.perf_counter()
    for frame in range(n):
        chain.upload_source(source.read())
        chain.render(bank.resolve_all(FEATURES), FEATURES, frame / 60.0)
    ctx.finish()
    ms = (time.perf_counter() - start) / n * 1000.0

    print(f"\n{CANVAS_W}x{CANVAS_H}, {len(effects)} effects + source blit")
    print(f"  {ms:.2f} ms/frame  ({1000.0 / ms:.0f} fps ceiling)")
    print(f"  budget at 60 fps: {ms / 16.67 * 100:.0f}% used")
    print(f"\nwrote {len(LOOKS)} stills + contact sheet to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
