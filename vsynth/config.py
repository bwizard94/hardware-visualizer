"""Global constants for the video synth.

Values here mirror hardware decisions already locked in the design doc, so the
prototype and the eventual carrier board agree on the same numbers.
"""

# Internal processing canvas. 720p was chosen to keep FPGA/GPU cost and
# latency down; NTSC/PAL input gets scaled to this before the effects chain.
CANVAS_W = 1280
CANVAS_H = 720

# Preview window on the dev machine. The hardware has no screen.
WINDOW_W = 1280
WINDOW_H = 720

# Audio pass-through is stereo, analysed per-channel (never summed to mono).
AUDIO_RATE = 48000
AUDIO_BLOCK = 512
AUDIO_CHANNELS = 2

# Analysis bands, in Hz. Four bands keeps the modulation matrix legible on a
# panel with no display.
BANDS = {
    "bass": (20, 160),
    "lowmid": (160, 800),
    "mid": (800, 3200),
    "high": (3200, 16000),
}

# Envelope smoothing, per analysis block. Fast attack, slower release, so
# visuals snap on transients but do not flicker.
ENV_ATTACK = 0.55
ENV_RELEASE = 0.12

TARGET_FPS = 60
