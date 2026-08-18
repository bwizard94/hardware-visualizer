// Video feedback: the previous finished frame, warped and decayed, summed back
// in. This is the camera-pointed-at-its-own-monitor effect, and it is the one
// place the chain reads a frame older than the current one.

uniform float u_mix;     // how much history returns
uniform float u_zoom;    // >1 pushes into the screen, <1 pulls out
uniform float u_rotate;  // radians per frame
uniform float u_decay;   // per-frame brightness retention

void main() {
    vec3 current = texture(u_input, v_uv).rgb;

    // Warp around the centre. Zoom and rotate compose into one matrix so the
    // tunnel spirals rather than stepping.
    vec2 c = v_uv - 0.5;
    float s = sin(u_rotate), co = cos(u_rotate);
    c = mat2(co, -s, s, co) * c / max(u_zoom, 0.01);
    vec2 huv = c + 0.5;

    // Outside the canvas there is no history; without this the edge smears.
    float inside = step(0.0, huv.x) * step(huv.x, 1.0)
                 * step(0.0, huv.y) * step(huv.y, 1.0);
    vec3 prev = texture(u_history, huv).rgb * u_decay * inside;

    // Loop gain. A raw additive loop settles at current/(1 - gain), which
    // clips to white for most of the Amount knob's travel -- the tunnel is
    // there but the exposure is gone. Attenuating the incoming image by most
    // of the loop gain keeps the steady state near unity, so the knob changes
    // how the feedback looks rather than just how blown out it is. The 0.85
    // leaves some real bloom at the top of the range instead of flattening it
    // to a plain crossfade.
    float gain = clamp(u_decay * u_mix, 0.0, 0.99);
    vec3 col = current * (1.0 - gain * 0.85) + prev * u_mix;

    f_color = vec4(min(col, vec3(1.0)), 1.0);
}
