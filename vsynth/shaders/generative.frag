// Generative layer: domain-warped value noise, coloured by phase-shifted
// cosines. This is the one stage that makes picture rather than processing it,
// so it works with no video input at all.
//
// It sits before feedback in the chain on purpose -- generated content that
// feeds back accumulates into structure, where blending it in afterwards would
// just lay a flat wash over everything.

uniform float u_amount;  // blend over whatever arrived from the previous stage
uniform float u_scale;   // feature size
uniform float u_speed;   // drift rate
uniform float u_warp;    // how hard the field folds through itself

float vnoise(vec2 p) {
    vec2 i = floor(p);
    vec2 f = fract(p);
    f = f * f * (3.0 - 2.0 * f);  // smoothstep, so cells do not show as creases
    float a = hash21(i);
    float b = hash21(i + vec2(1.0, 0.0));
    float c = hash21(i + vec2(0.0, 1.0));
    float d = hash21(i + vec2(1.0, 1.0));
    return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
}

float fbm(vec2 p) {
    float sum = 0.0;
    float amp = 0.5;
    for (int i = 0; i < 4; i++) {
        sum += vnoise(p) * amp;
        p *= 2.03;   // not exactly 2, to avoid the octaves lining up on a grid
        amp *= 0.5;
    }
    return sum;
}

void main() {
    vec2 p = v_uv * u_scale;
    p.x *= u_res.x / u_res.y;
    float t = u_time * u_speed;

    // Warp the sample position by another noise field. This is what turns
    // flat clouds into the folded, liquid structure worth looking at.
    vec2 q = vec2(fbm(p + t * 0.3), fbm(p + vec2(5.2, 1.3) - t * 0.2));
    float n = fbm(p + q * u_warp + t * 0.1);

    // Bass pushes the colour phase, so the palette moves with the track
    // whether or not anything is patched into the modulation matrix.
    vec3 gen = 0.5 + 0.5 * cos(6.28318 * (n + vec3(0.0, 0.33, 0.67) + u_bands.x * 0.5));

    vec3 base = texture(u_input, v_uv).rgb;
    f_color = vec4(mix(base, gen, u_amount), 1.0);
}
