// Master output stage: blends the processed image back against the untouched
// canvas, then applies output level.
//
// A dry/wet fader across the whole chain is the one control that reliably
// rescues a live set -- it pulls everything back to clean picture in a single
// gesture without disturbing any effect setting.

uniform sampler2D u_dry;  // canvas straight out of the mixer, pre-effects
uniform float u_wet;
uniform float u_level;

void main() {
    vec3 dry = texture(u_dry, v_uv).rgb;
    vec3 wet = texture(u_input, v_uv).rgb;
    f_color = vec4(clamp(mix(dry, wet, u_wet) * u_level, 0.0, 1.0), 1.0);
}
