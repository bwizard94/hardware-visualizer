// Colour stage: hue rotation, saturation, gain and posterisation.
// Sits last in the chain so it grades whatever glitch and feedback produced,
// the same way a colouriser sits at the end of a modular video patch.

uniform float u_hue;        // full rotation over 0..1
uniform float u_sat;
uniform float u_gain;
uniform float u_posterize;  // levels; high values are effectively off

vec3 hue_rotate(vec3 col, float angle) {
    // Rotation about the luma axis in YIQ. Cheaper than a full RGB->HSV->RGB
    // round trip and it preserves perceived brightness better.
    const vec3 k = vec3(0.57735);
    float c = cos(angle);
    return col * c + cross(k, col) * sin(angle) + k * dot(k, col) * (1.0 - c);
}

void main() {
    vec3 col = texture(u_input, v_uv).rgb;

    col = hue_rotate(col, u_hue * 6.28318);

    float luma = dot(col, vec3(0.2126, 0.7152, 0.0722));
    col = mix(vec3(luma), col, u_sat);

    col *= u_gain;

    // Posterise last, so it quantises the final graded colour.
    float levels = max(u_posterize, 2.0);
    col = floor(col * levels + 0.5) / levels;

    f_color = vec4(clamp(col, 0.0, 1.0), 1.0);
}
