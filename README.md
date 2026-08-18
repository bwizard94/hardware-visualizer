# vsynth

Software core for an audio-reactive video synthesizer — a standalone hardware
instrument for live performance, not a single-effect glitch box.

This repo is the **effects engine**, developed on a laptop ahead of the
hardware. Every visual effect is a GLSL fragment shader, so the same code moves
to whichever compute core the build settles on. Running it also answers the
question the hardware decision is currently blocked on: how much GPU work the
effects chain actually costs at 720p.

![four looks from the effects chain](previews/contact_sheet.png)

*Bypassed, glitch only, feedback only, and the full chain — rendered headless
off the built-in test pattern by `tools/preview.py`.*

## Status

Working vertical slice: video source → effects chain → output, with live audio
analysis and MIDI both driving parameters.

| Subsystem | State |
|---|---|
| Render chain (ping-pong FBOs, 720p, 16-bit float) | working |
| Effects: glitch, feedback, colour | working |
| Effects: kaleidoscope, generative | not started |
| Audio analysis (4 bands + transients, independent L/R) | working |
| MIDI CC routing + MIDI learn | working |
| MIDI clock tracking | tracked, not yet used by any effect |
| Presets / scene recall | save + load; one slot |
| Sources: webcam, test pattern | working |
| Sources: video files, dual RGBS capture | not started |

## Running it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

```bash
.venv/bin/python -m vsynth
```

First launch asks for camera and microphone permission. If the camera is
refused or absent it falls back to colour bars, so it always starts.

See what hardware it found:

```bash
.venv/bin/python -m vsynth --list
```

Useful flags: `--no-camera`, `--camera N`, `--audio "<device>"`,
`--midi "<port substring>"`.

### Controls

The finished panel has 24 pots, 3 crossfaders, 12 illuminated buttons and no
screen. The keyboard here is scaffolding until that exists.

| Key | Action |
|---|---|
| `TAB` / `shift-TAB` | select parameter (stands in for "which pot") |
| arrows | adjust selected parameter (hold shift for fine) |
| `1` `2` `3` | bypass glitch / feedback / colour |
| `M` | cycle the selected parameter's modulation source |
| `[` `]` | modulation depth down / up |
| `L` | MIDI learn — then move a control on your controller |
| `K` | clear MIDI bindings for the selected parameter |
| `S` / `R` | save / reload preset and bindings |
| `T` | toggle camera ↔ test pattern |
| `P` | print the current patch |
| `ESC` | quit |

## How it fits together

```
source ──► canvas ──► glitch ──► feedback ──► colour ──► out
                                    ▲                     │
                                    └── history ◄─────────┘
              audio ──► bands + transients ──┐
                                             ├──► parameters
              MIDI ──► CC ──────────────────┘
```

Everything meets at the parameter, which is the one idea worth understanding
before changing anything:

**A parameter's knob position is always 0..1** — literally what a pot reads.
Modulation is summed in that same normalised space, clamped, and only then
scaled to whatever range the shader wants. So a physical pot, a MIDI CC and an
audio band all write to the same place without any of them knowing the
parameter's units. Adding a control to the panel later changes no shader code.

Audio L and R are analysed **independently and never summed**, matching the
hardware decision that each signal path stays separately patchable. Modulation
sources are named `l.*`, `r.*` and `mix.*` — bands `bass`/`lowmid`/`mid`/`high`
plus `rms` and `hit` (transient). Band levels are auto-gained against a slowly
decaying peak, so a quiet synth patch and a hot drum bus drive the visuals
about equally.

## Layout

```
vsynth/
  config.py            canvas size, sample rate, band edges
  app.py               window, render loop, keyboard
  engine/
    params.py          Param + ParamBank: knob position, modulation, presets
    effect.py          effect definition, GLSL loading, shared shader header
    chain.py           ping-pong render targets + history buffer
    patch.py           which effects exist, their defaults and panel order
  shaders/*.frag       one file per effect
  audio/analyzer.py    FFT bands, transient detection, auto-gain
  midi/router.py       CC routing, MIDI learn, clock
  sources/video.py     webcam (threaded) and test pattern
tools/
  smoke_test.py        headless: compile every shader, verify the chain renders
  preview.py           headless: render stills of several looks, time the chain
```

## Adding an effect

1. Write `vsynth/shaders/<name>.frag`. The shared header already gives you
   `u_input`, `u_history`, `u_res`, `u_time`, `u_bands`, `u_hit` and `hash21()`
   — declare only your own `u_<param>` uniforms.
2. Add an `Effect(...)` with its `ParamSpec` list in `engine/patch.py`.

Presets, MIDI learn and the modulation matrix pick it up with no further
wiring. Run `tools/smoke_test.py` before opening a window — a GLSL compile
error reads much better there than as a black screen.

## Porting notes

Shaders are GLSL 330 core but stay inside the GLES 3.0 feature set — no
compute, no dynamic sampler indexing, no `textureGather`. Moving to a Pi-class
GPU should mean changing `VERSION_HEADER` in `engine/effect.py` to
`#version 300 es` and adding precision qualifiers, not rewriting effects.

Hardware constants live in `config.py` so the prototype and the eventual
carrier board agree on the same numbers.
