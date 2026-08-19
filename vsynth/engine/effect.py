"""Effect definition and GLSL program loading.

An effect is a single fullscreen fragment shader plus the parameters that drive
it. Declaring the parameters alongside the shader means adding an effect later
is one .frag file and one spec list -- the panel, MIDI learn and preset system
pick it up with no further wiring.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import moderngl

from .params import Param, ParamBank

SHADER_DIR = Path(__file__).resolve().parent.parent / "shaders"

# Written against GLSL 330 core for desktop. Every effect stays inside the
# GLES 3.0 feature set (no compute, no dynamic indexing of samplers, no
# textureGather) so the same shaders run on a Pi-class GPU unchanged -- only
# this header line has to swap to "#version 300 es".
VERSION_HEADER = "#version 330 core\n"

VERTEX_SHADER = VERSION_HEADER + """
in vec2 in_pos;
out vec2 v_uv;
void main() {
    v_uv = in_pos * 0.5 + 0.5;
    gl_Position = vec4(in_pos, 0.0, 1.0);
}
"""

# Uniforms every effect can rely on, injected so no shader has to redeclare
# them. u_bands/u_hit are here so a shader can react to audio directly, on top
# of whatever the modulation matrix is already doing to its parameters.
COMMON_HEADER = """
in vec2 v_uv;
out vec4 f_color;

uniform sampler2D u_input;    // output of the previous stage
uniform sampler2D u_history;  // final canvas of the previous frame
uniform vec2  u_res;
uniform float u_time;
uniform vec4  u_bands;        // bass, lowmid, mid, high (max of L/R)
uniform vec2  u_hit;          // transient envelope, L and R
// Musical clock: beat phase 0..1, bar phase 0..1, beat pulse, total beats.
// w counts up without wrapping, so quantising it gives a stable step index
// rather than one that restarts every beat.
uniform vec4  u_clock;

// Cheap hash, used for glitch block selection and noise.
float hash21(vec2 p) {
    p = fract(p * vec2(123.34, 456.21));
    p += dot(p, p + 45.32);
    return fract(p.x * p.y);
}
"""


@dataclass
class ParamSpec:
    name: str
    label: str
    lo: float = 0.0
    hi: float = 1.0
    base: float = 0.0
    mod_source: str | None = None
    mod_depth: float = 0.0
    fader: bool = False


class Effect:
    def __init__(
        self,
        key: str,
        label: str,
        shader_file: str,
        specs: list[ParamSpec],
        bank: ParamBank,
        enabled: bool = True,
    ) -> None:
        self.key = key
        self.label = label
        self.shader_file = shader_file
        self.enabled = enabled
        self.program: moderngl.Program | None = None

        # (uniform name, param key) pairs, resolved once at construction.
        self.uniforms: list[tuple[str, str]] = []
        for spec in specs:
            full_key = f"{key}.{spec.name}"
            bank.add(
                Param(
                    key=full_key,
                    label=spec.label,
                    group=key,
                    lo=spec.lo,
                    hi=spec.hi,
                    base=spec.base,
                    mod_source=spec.mod_source,
                    mod_depth=spec.mod_depth,
                    fader=spec.fader,
                )
            )
            self.uniforms.append((f"u_{spec.name}", full_key))

    def build(self, ctx: moderngl.Context) -> None:
        source = (SHADER_DIR / self.shader_file).read_text()
        fragment = VERSION_HEADER + COMMON_HEADER + source
        try:
            self.program = ctx.program(vertex_shader=VERTEX_SHADER, fragment_shader=fragment)
        except Exception as exc:
            raise RuntimeError(f"{self.shader_file} failed to compile:\n{exc}") from exc

    def set_uniforms(self, values: dict[str, float]) -> None:
        """Push resolved parameter values. Unused uniforms get optimised out by
        the GLSL compiler, so a missing name is normal, not an error."""
        assert self.program is not None
        for uniform_name, param_key in self.uniforms:
            member = self.program.get(uniform_name, None)
            if member is not None:
                member.value = values[param_key]
