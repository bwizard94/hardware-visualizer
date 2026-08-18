"""The effects chain: ping-pong render targets plus a one-frame history buffer.

Structure mirrors the hardware signal path -- source is digitised into the
canvas once, every effect is a pass over that canvas, and the finished frame is
what goes out (and what feedback sees next frame).
"""

from __future__ import annotations

import moderngl
import numpy as np

from ..config import CANVAS_H, CANVAS_W
from .effect import VERSION_HEADER, VERTEX_SHADER, Effect
from .params import ParamBank

# Plain copy, used to load the source into the canvas and to draw the finished
# canvas to the window.
BLIT_FRAGMENT = VERSION_HEADER + """
in vec2 v_uv;
out vec4 f_color;
uniform sampler2D u_input;
void main() { f_color = texture(u_input, v_uv); }
"""


class Chain:
    def __init__(self, ctx: moderngl.Context, effects: list[Effect], bank: ParamBank) -> None:
        self.ctx = ctx
        self.effects = effects
        self.bank = bank

        # Fullscreen triangle pair. in_pos is clip space; the vertex shader
        # derives UVs from it.
        quad = np.array(
            [-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1],
            dtype="f4",
        )
        self.vbo = ctx.buffer(quad.tobytes())

        # 16-bit float internally: feedback accumulates over many frames, and
        # 8-bit quantises into visible banding after a few passes.
        self.targets = [self._make_target() for _ in range(2)]
        self.history_tex, self.history_fbo = self._make_target()

        self.source_tex = ctx.texture((CANVAS_W, CANVAS_H), 3, dtype="f1")
        self.source_tex.repeat_x = False
        self.source_tex.repeat_y = False

        self.blit = ctx.program(vertex_shader=VERTEX_SHADER, fragment_shader=BLIT_FRAGMENT)
        self.blit_vao = ctx.vertex_array(self.blit, [(self.vbo, "2f", "in_pos")])

        for effect in self.effects:
            effect.build(ctx)
        self.vaos = {
            e.key: ctx.vertex_array(e.program, [(self.vbo, "2f", "in_pos")])
            for e in self.effects
        }

    def _make_target(self):
        tex = self.ctx.texture((CANVAS_W, CANVAS_H), 4, dtype="f2")
        tex.repeat_x = False
        tex.repeat_y = False
        tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        return tex, self.ctx.framebuffer(color_attachments=[tex])

    def upload_source(self, frame: np.ndarray) -> None:
        self.source_tex.write(frame.tobytes())

    def render(self, values: dict[str, float], features: dict[str, float], now: float):
        """Run the chain; returns the texture holding the finished canvas."""
        bands = (
            features.get("mix.bass", 0.0),
            features.get("mix.lowmid", 0.0),
            features.get("mix.mid", 0.0),
            features.get("mix.high", 0.0),
        )
        hit = (features.get("l.hit", 0.0), features.get("r.hit", 0.0))

        # Stage 0: source into the canvas, so every effect reads a canvas
        # texture and the ping-pong below has a uniform starting point.
        dst = 0
        tex, fbo = self.targets[dst]
        fbo.use()
        self.source_tex.use(0)
        self.blit["u_input"].value = 0
        self.blit_vao.render(moderngl.TRIANGLES)
        current = tex
        dst ^= 1

        for effect in self.effects:
            if not effect.enabled:
                continue
            tex, fbo = self.targets[dst]
            fbo.use()

            current.use(0)
            self.history_tex.use(1)
            prog = effect.program
            prog["u_input"].value = 0
            if (member := prog.get("u_history", None)) is not None:
                member.value = 1
            for name, value in (
                ("u_res", (float(CANVAS_W), float(CANVAS_H))),
                ("u_time", now),
                ("u_bands", bands),
                ("u_hit", hit),
            ):
                if (member := prog.get(name, None)) is not None:
                    member.value = value
            effect.set_uniforms(values)

            self.vaos[effect.key].render(moderngl.TRIANGLES)
            current = tex
            dst ^= 1

        # Snapshot the finished frame for next frame's feedback tap. A GPU-side
        # framebuffer copy is cheaper than another shader pass.
        src_fbo = self.targets[0][1] if current is self.targets[0][0] else self.targets[1][1]
        self.ctx.copy_framebuffer(self.history_fbo, src_fbo)
        return current

    def to_screen(self, tex, screen, viewport) -> None:
        screen.use()
        screen.clear(0.0, 0.0, 0.0, 1.0)
        self.ctx.viewport = viewport  # letterbox after clearing the full window
        tex.use(0)
        self.blit["u_input"].value = 0
        self.blit_vao.render(moderngl.TRIANGLES)

    def release(self) -> None:
        for tex, fbo in [*self.targets, (self.history_tex, self.history_fbo)]:
            fbo.release()
            tex.release()
        self.source_tex.release()
        self.vbo.release()
