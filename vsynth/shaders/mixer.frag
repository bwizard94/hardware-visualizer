// Source mixer: composites the two video inputs into the canvas.
//
// On the hardware these are the two RGBS inputs, digitised before compositing
// so the blend happens in the same digital core as everything else -- which is
// what lets audio and MIDI modulate the crossfade itself, not just each source
// underneath it.

uniform sampler2D u_input_b;
uniform float u_xfade;   // 0 = A only, 1 = B only
uniform float u_mode;    // stepped blend mode, see below
uniform float u_gain_b;  // trim, since two sources rarely match in level

void main() {
    vec3 a = texture(u_input, v_uv).rgb;
    vec3 b = texture(u_input_b, v_uv).rgb * u_gain_b;

    int mode = int(u_mode + 0.5);
    vec3 combined;

    if (mode == 1) {          // ADD -- B piles onto A, blows out on purpose
        combined = a + b;
    } else if (mode == 2) {   // DIFFERENCE -- edges where the sources disagree
        combined = abs(a - b);
    } else if (mode == 3) {   // MULTIPLY -- B darkens and tints A
        combined = a * b;
    } else if (mode == 4) {   // LUMA KEY -- B punches through where it is bright
        float key = smoothstep(0.35, 0.65, dot(b, vec3(0.2126, 0.7152, 0.0722)));
        combined = mix(a, b, key);
    } else {                  // MIX -- plain crossfade
        combined = b;
    }

    // Every mode rides the same fader: at 0 you always get A untouched, and at
    // 1 you always get the mode's full result. That keeps the fader's feel
    // consistent while the mode knob changes what it fades into.
    f_color = vec4(clamp(mix(a, combined, u_xfade), 0.0, 1.0), 1.0);
}
