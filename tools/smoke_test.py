"""Headless check of the shader chain: compiles every effect and renders a few
frames offscreen. Run this before opening a window -- a GLSL error here is far
easier to read than a black window."""

from __future__ import annotations

import sys

import moderngl
import numpy as np

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from vsynth.config import CANVAS_H, CANVAS_W
from vsynth.engine.chain import Chain
from vsynth.engine.patch import build_patch
from vsynth.sources.video import TestPattern


def main() -> int:
    ctx = moderngl.create_standalone_context(require=330)
    print(f"GL {ctx.info['GL_VERSION']}")
    print(f"   {ctx.info['GL_RENDERER']}")

    bank, effects = build_patch()
    print(f"patch: {len(effects)} effects, {len(bank)} parameters")

    chain = Chain(ctx, effects, bank)
    print("all shaders compiled")

    source = TestPattern()
    # Non-zero audio features, so modulated parameters are actually exercised
    # rather than sitting at their unmodulated defaults.
    features = {k: 0.5 for k in
                ["mix.bass", "mix.lowmid", "mix.mid", "mix.high", "mix.hit", "l.hit", "r.hit"]}

    for frame in range(8):
        chain.upload_source(source.read())
        values = bank.resolve_all(features)
        final = chain.render(values, features, frame / 60.0)

    fbo = ctx.framebuffer(color_attachments=[final])
    data = np.frombuffer(fbo.read(components=3, dtype="f1"), dtype=np.uint8)
    data = data.reshape(CANVAS_H, CANVAS_W, 3)

    print(f"output: {data.shape}, mean={data.mean():.1f}, "
          f"min={data.min()}, max={data.max()}, unique={len(np.unique(data))}")

    if data.max() == 0:
        print("FAIL: output is entirely black")
        return 1
    if len(np.unique(data)) < 8:
        print("FAIL: output has almost no variation, chain is probably not running")
        return 1

    # Feedback must actually accumulate across frames, so verify the history
    # path by turning it up and confirming the image changes.
    bank.get("feedback.mix").base = 0.9
    before = data.copy()
    for frame in range(8, 24):
        chain.upload_source(source.read())
        final = chain.render(bank.resolve_all(features), features, frame / 60.0)
    after = np.frombuffer(
        ctx.framebuffer(color_attachments=[final]).read(components=3, dtype="f1"),
        dtype=np.uint8).reshape(CANVAS_H, CANVAS_W, 3)

    delta = float(np.abs(after.astype(int) - before.astype(int)).mean())
    print(f"feedback delta: {delta:.2f}")
    if delta < 1.0:
        print("FAIL: raising feedback.mix changed nothing; history buffer is dead")
        return 1

    print("\nPASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
