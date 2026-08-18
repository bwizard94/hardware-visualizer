// Kaleidoscope: polar mirror fold.
// Folding happens in aspect-corrected space -- doing it directly on 16:9 UVs
// shears every segment, which reads as a mistake rather than a pattern.

uniform float u_segments;  // wedges around the circle
uniform float u_rotate;    // spin rate; centre detent is stationary
uniform float u_zoom;      // how much of the source each wedge samples
uniform float u_mix;       // blend back against the unfolded image

const float TAU = 6.28318530718;

void main() {
    float aspect = u_res.x / u_res.y;

    vec2 p = v_uv - 0.5;
    p.x *= aspect;

    float radius = length(p);
    // Rate rather than absolute angle: a knob centred at zero holds the
    // pattern still and spins either way off centre, which is the more useful
    // gesture live than dialling in a fixed rotation.
    float angle = atan(p.y, p.x) + u_rotate * u_time;

    // Fold the circle into one wedge, then mirror within it. Without the
    // mirror the seams are hard cuts; with it they meet, which is what makes
    // the pattern continuous.
    float wedge = TAU / max(u_segments, 2.0);
    angle = mod(angle, wedge);
    angle = abs(angle - wedge * 0.5);

    p = vec2(cos(angle), sin(angle)) * radius * u_zoom;
    p.x /= aspect;
    vec2 uv = p + 0.5;

    // Textures are clamp-to-edge, so out-of-range wedges smear the border
    // rather than wrapping into unrelated parts of the frame.
    vec3 folded = texture(u_input, clamp(uv, 0.0, 1.0)).rgb;
    vec3 plain = texture(u_input, v_uv).rgb;

    f_color = vec4(mix(plain, folded, u_mix), 1.0);
}
