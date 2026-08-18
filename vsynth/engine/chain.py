"""The effects chain.

Signal path, matching the hardware:

    source A ─┐
              ├─ mixer ─► dry ─► effects... ─► master ─► out
    source B ─┘                    ▲                      │
                                   └──── history ◄────────┘

Both sources are digitised into the canvas before compositing, so the crossfade
happens in the same domain as the effects and can be modulated like anything
else. The mixer and master are structural rather than optional, so they live
outside the bypassable effects list -- but they are ordinary Effect objects, so
their parameters are MIDI-learnable and saved in presets like any other.
"""

from __future__ import annotations

import moderngl
import numpy as np

from ..config import CANVAS_H, CANVAS_W
from .effect import VERSION_HEADER, VERTEX_SHADER, Effect
from .params import ParamBank

BLIT_FRAGMENT = VERSION_HEADER + """
in vec2 v_uv;
out vec4 f_color;
uniform sampler2D u_input;
void main() { f_color = texture(u_input, v_uv); }
"""

# Texture units. Unit 2 is whatever second image a stage needs -- source B for
# the mixer, the dry canvas for the master.
UNIT_INPUT = 0
UNIT_HISTORY = 1
UNIT_AUX = 2


class Chain:
    def __init__(self, ctx: moderngl.Context, effects: list[Effect],
                 mixer: Effect, master: Effect, bank: ParamBank) -> None:
        self.ctx = ctx
        self.effects = effects
        self.mixer = mixer
        self.master = master
        self.bank = bank

        quad = np.array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1], dtype="f4")
        self.vbo = ctx.buffer(quad.tobytes())

        # 16-bit float internally: feedback accumulates over many frames and
        # 8-bit quantises into visible banding after a few passes.
        self.dry_tex, self.dry_fbo = self._make_target()
        self.targets = [self._make_target() for _ in range(2)]
        self.history_tex, self.history_fbo = self._make_target()

        self.source_a = self._make_source_texture()
        self.source_b = self._make_source_texture()

        self.blit = ctx.program(vertex_shader=VERTEX_SHADER, fragment_shader=BLIT_FRAGMENT)
        self.blit_vao = ctx.vertex_array(self.blit, [(self.vbo, "2f", "in_pos")])

        self.vaos = {}
        for effect in [*self.effects, self.mixer, self.master]:
            effect.build(ctx)
            self.vaos[effect.key] = ctx.vertex_array(
                effect.program, [(self.vbo, "2f", "in_pos")]
            )

    def _make_target(self):
        tex = self.ctx.texture((CANVAS_W, CANVAS_H), 4, dtype="f2")
        tex.repeat_x = False
        tex.repeat_y = False
        tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        return tex, self.ctx.framebuffer(color_attachments=[tex])

    def _make_source_texture(self):
        tex = self.ctx.texture((CANVAS_W, CANVAS_H), 3, dtype="f1")
        tex.repeat_x = False
        tex.repeat_y = False
        return tex

    def upload_sources(self, frame_a: np.ndarray, frame_b: np.ndarray) -> None:
        self.source_a.write(frame_a.tobytes())
        self.source_b.write(frame_b.tobytes())

    # --- rendering ---------------------------------------------------------

    @staticmethod
    def _set(prog, name, value) -> None:
        """Set a uniform if the shader actually uses it. Unused uniforms are
        optimised out by the GLSL compiler, so absence is normal."""
        member = prog.get(name, None)
        if member is not None:
            member.value = value

    def _bind_common(self, prog, now: float, bands, hit) -> None:
        self._set(prog, "u_input", UNIT_INPUT)
        self._set(prog, "u_history", UNIT_HISTORY)
        self._set(prog, "u_res", (float(CANVAS_W), float(CANVAS_H)))
        self._set(prog, "u_time", now)
        self._set(prog, "u_bands", bands)
        self._set(prog, "u_hit", hit)

    def render(self, values: dict[str, float], features: dict[str, float], now: float):
        """Run the whole path; returns the texture holding the finished frame."""
        bands = (
            features.get("mix.bass", 0.0),
            features.get("mix.lowmid", 0.0),
            features.get("mix.mid", 0.0),
            features.get("mix.high", 0.0),
        )
        hit = (features.get("l.hit", 0.0), features.get("r.hit", 0.0))

        # Mixer: both sources into the dry canvas.
        self.dry_fbo.use()
        self.source_a.use(UNIT_INPUT)
        self.source_b.use(UNIT_AUX)
        self._bind_common(self.mixer.program, now, bands, hit)
        self._set(self.mixer.program, "u_input_b", UNIT_AUX)
        self.mixer.set_uniforms(values)
        self.vaos[self.mixer.key].render(moderngl.TRIANGLES)

        # Effects, ping-ponging between the two spare targets. Reading straight
        # from the dry canvas means a fully bypassed chain costs no passes.
        current, current_fbo = self.dry_tex, self.dry_fbo
        dst = 0
        for effect in self.effects:
            if not effect.enabled:
                continue
            tex, fbo = self.targets[dst]
            fbo.use()
            current.use(UNIT_INPUT)
            self.history_tex.use(UNIT_HISTORY)
            self._bind_common(effect.program, now, bands, hit)
            effect.set_uniforms(values)
            self.vaos[effect.key].render(moderngl.TRIANGLES)
            current, current_fbo = tex, fbo
            dst ^= 1

        # Master: blend the processed image against the dry canvas. It writes
        # into whichever target the chain is not holding, so there is never a
        # read-write hazard even with every effect bypassed.
        final_tex, final_fbo = self.targets[dst]
        final_fbo.use()
        current.use(UNIT_INPUT)
        self.dry_tex.use(UNIT_AUX)
        self._bind_common(self.master.program, now, bands, hit)
        self._set(self.master.program, "u_dry", UNIT_AUX)
        self.master.set_uniforms(values)
        self.vaos[self.master.key].render(moderngl.TRIANGLES)

        # History is the finished frame, i.e. what actually leaves the output
        # jacks -- the same thing a camera pointed at the monitor would see.
        self.ctx.copy_framebuffer(self.history_fbo, final_fbo)
        return final_tex

    def to_screen(self, tex, screen, viewport) -> None:
        screen.use()
        screen.clear(0.0, 0.0, 0.0, 1.0)
        self.ctx.viewport = viewport  # letterbox after clearing the full window
        tex.use(UNIT_INPUT)
        self.blit["u_input"].value = UNIT_INPUT
        self.blit_vao.render(moderngl.TRIANGLES)

    def release(self) -> None:
        pairs = [*self.targets, (self.dry_tex, self.dry_fbo),
                 (self.history_tex, self.history_fbo)]
        for tex, fbo in pairs:
            fbo.release()
            tex.release()
        self.source_a.release()
        self.source_b.release()
        self.vbo.release()
