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

ALL = ["glitch", "kaleido", "generative", "feedback", "color"]


def only(*keep: str) -> list[str]:
    """Bypass everything except the named effects."""
    return [k for k in ALL if k not in keep]


# name -> (bypassed effects, knob overrides). Each is a plausible performance
# patch rather than a parameter sweep.
LOOKS = {
    "01_source_a":  (only(), {}),
    "02_crossfade": (only(), {"mixer.xfade": 0.55, "mixer.mode": 0.5}),  # difference
    "03_glitch":    (only("glitch"), {"glitch.amount": 0.75, "glitch.blocks": 0.45,
                                      "glitch.shift": 0.5, "glitch.scan": 0.4}),
    # Folded against a mix of both sources: flat bars alone give the fold
    # nothing to work with and read as plain concentric polygons.
    "04_kaleido":   (only("kaleido"), {"mixer.xfade": 0.5, "mixer.mode": 0.5,
                                       "kaleido.mix": 1.0, "kaleido.segments": 0.4,
                                       "kaleido.zoom": 0.45}),
    "05_generative": (only("generative"), {"generative.amount": 1.0, "generative.scale": 0.35,
                                           "generative.warp": 0.55, "generative.speed": 0.4}),
    "06_full":      (only(*ALL), {"mixer.xfade": 0.35,
                                  "glitch.amount": 0.35, "glitch.shift": 0.35,
                                  "kaleido.mix": 0.7, "kaleido.segments": 0.35,
                                  "generative.amount": 0.4, "generative.warp": 0.5,
                                  "feedback.mix": 0.6, "feedback.zoom": 0.66,
                                  "feedback.rotate": 0.56, "feedback.decay": 0.82,
                                  "color.hue": 0.15, "color.sat": 0.55}),
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
    bank, effects, mixer, master = build_patch()
    chain = Chain(ctx, effects, mixer, master, bank)
    source_a = TestPattern("bars")
    source_b = TestPattern("grid")
    tiles = []

    for name, (bypass, overrides) in LOOKS.items():
        defaults = {p.key: p.base for p in bank}
        for effect in effects:
            effect.enabled = effect.key not in bypass
        for key, value in overrides.items():
            bank.get(key).base = value

        # Feedback needs history to accumulate before it looks like anything.
        for frame in range(60):
            chain.upload_sources(source_a.read(), source_b.read())
            final = chain.render(bank.resolve_all(FEATURES), FEATURES, frame / 60.0)

        img = read_rgb(ctx, final)
        cv2.imwrite(str(OUT / f"{name}.png"), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
        label = img.copy()
        text = name[3:].replace("_", " ")
        cv2.putText(label, text, (24, 72), cv2.FONT_HERSHEY_SIMPLEX,
                    2.0, (0, 0, 0), 9, cv2.LINE_AA)
        cv2.putText(label, text, (24, 72), cv2.FONT_HERSHEY_SIMPLEX,
                    2.0, (255, 255, 255), 4, cv2.LINE_AA)
        tiles.append(cv2.resize(label, (CANVAS_W // 2, CANVAS_H // 2)))
        print(f"  {name}: mean={img.mean():.1f}")

        for p in bank:
            p.base = defaults[p.key]

    sheet = np.vstack([np.hstack(tiles[:3]), np.hstack(tiles[3:])])
    cv2.imwrite(str(OUT / "contact_sheet.png"), cv2.cvtColor(sheet, cv2.COLOR_RGB2BGR))

    # Timing. Source generation and GPU render are measured separately: they
    # run on different processors, and folding them into one number once hid a
    # 7 ms CPU cost behind what looked like a GPU result.
    for effect in effects:
        effect.enabled = True
    for _ in range(20):  # warm up shader and pipeline caches
        chain.upload_sources(source_a.read(), source_b.read())
        chain.render(bank.resolve_all(FEATURES), FEATURES, 0.0)
    ctx.finish()

    n = 200
    start = time.perf_counter()
    for _ in range(n):
        frames = (source_a.read(), source_b.read())
    cpu_ms = (time.perf_counter() - start) / n * 1000.0

    start = time.perf_counter()
    for frame in range(n):
        chain.upload_sources(*frames)
        chain.render(bank.resolve_all(FEATURES), FEATURES, frame / 60.0)
    ctx.finish()
    gpu_ms = (time.perf_counter() - start) / n * 1000.0

    print(f"\n{CANVAS_W}x{CANVAS_H}, {len(effects)} effects + mixer + master")
    print(f"  GPU render      {gpu_ms:5.2f} ms/frame  ({1000.0 / gpu_ms:.0f} fps ceiling)")
    print(f"  source (CPU)    {cpu_ms:5.2f} ms/frame")
    total = gpu_ms + cpu_ms
    print(f"  total           {total:5.2f} ms/frame  -- {total / 16.67 * 100:.0f}% of the 60 fps budget")

    print(f"\nwrote {len(LOOKS)} stills + contact sheet to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
