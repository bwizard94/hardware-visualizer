// Glitch: horizontal block displacement, RGB channel separation, scanlines.
// Block tearing and channel split are the two artefacts that read clearly at
// projection distance; finer digital noise disappears on a big screen.

uniform float u_amount;   // how many blocks tear, 0..1
uniform float u_blocks;   // block height in scanline-ish rows
uniform float u_shift;    // RGB channel separation, in UV
uniform float u_scan;     // scanline depth

void main() {
    vec2 uv = v_uv;

    // Quantise to horizontal bands, then hash each band against time so the
    // tearing re-rolls rather than sitting still.
    float band = floor(uv.y * u_blocks);
    float roll = hash21(vec2(band, floor(u_time * 12.0)));
    float torn = step(1.0 - u_amount, roll);
    float offset = (hash21(vec2(band, floor(u_time * 12.0) + 7.0)) - 0.5) * 0.35;
    uv.x = fract(uv.x + torn * offset * u_amount);

    // Channel separation, pushed slightly further on torn bands so the split
    // tracks the tearing instead of reading as a separate effect.
    float split = u_shift * (1.0 + torn * 2.0);
    float r = texture(u_input, uv + vec2(split, 0.0)).r;
    float g = texture(u_input, uv).g;
    float b = texture(u_input, uv - vec2(split, 0.0)).b;
    vec3 col = vec3(r, g, b);

    // Scanlines computed from real pixel rows, so density does not change if
    // the canvas resolution does.
    float line = sin(v_uv.y * u_res.y * 3.14159);
    col *= 1.0 - u_scan * 0.5 * (0.5 + 0.5 * line);

    f_color = vec4(col, 1.0);
}
