"""Real-time audio analysis for the pass-through signal.

L and R are analysed independently and never summed, matching the hardware
decision: each audio path stays separately patchable, so a synth can send a
control-type signal down one channel without it touching the other.

All analysis runs inside the PortAudio callback -- two 2048-point real FFTs per
block is cheap, and it avoids a queue and its latency between the tap and the
visuals.
"""

from __future__ import annotations

import numpy as np
import sounddevice as sd

from ..config import AUDIO_BLOCK, AUDIO_CHANNELS, AUDIO_RATE, BANDS, ENV_ATTACK, ENV_RELEASE

FFT_SIZE = 2048  # ~23 Hz bins, fine enough to resolve the bass band


class ChannelState:
    """Per-channel analysis state. One of these per side, never shared."""

    def __init__(self, freqs: np.ndarray) -> None:
        # Precompute which FFT bins belong to which band, once.
        self.band_slices = {
            name: np.where((freqs >= lo) & (freqs < hi))[0]
            for name, (lo, hi) in BANDS.items()
        }
        self.ring = np.zeros(FFT_SIZE, dtype=np.float32)
        self.env = {name: 0.0 for name in BANDS}
        # Auto-gain: a slowly-decaying peak per band, so the visuals respond the
        # same way to a quiet synth patch and a hot drum bus.
        self.peak = {name: 1e-4 for name in BANDS}
        self.rms = 0.0
        self.rms_peak = 1e-4
        self.prev_mags = np.zeros(len(BANDS), dtype=np.float32)
        self.flux_avg = 0.0
        self.hit = 0.0  # transient envelope, decays each block

    def process(self, block: np.ndarray, window: np.ndarray) -> None:
        n = len(block)
        self.ring = np.roll(self.ring, -n)
        self.ring[-n:] = block

        spectrum = np.abs(np.fft.rfft(self.ring * window))

        mags = np.empty(len(BANDS), dtype=np.float32)
        for i, (name, idx) in enumerate(self.band_slices.items()):
            mag = float(spectrum[idx].mean()) if idx.size else 0.0
            mags[i] = mag

            self.peak[name] = max(mag, self.peak[name] * 0.9995)
            norm = min(1.0, mag / max(self.peak[name], 1e-6))

            # Fast attack, slow release: snap on hits, ride out gaps.
            coeff = ENV_ATTACK if norm > self.env[name] else ENV_RELEASE
            self.env[name] += (norm - self.env[name]) * coeff

        raw_rms = float(np.sqrt(np.mean(block.astype(np.float64) ** 2)))
        self.rms_peak = max(raw_rms, self.rms_peak * 0.9995)
        self.rms = min(1.0, raw_rms / max(self.rms_peak, 1e-6))

        # Spectral flux: only rising energy counts as onset evidence.
        flux = float(np.sum(np.maximum(mags - self.prev_mags, 0.0)))
        self.prev_mags = mags
        self.flux_avg = self.flux_avg * 0.95 + flux * 0.05

        self.hit *= 0.80  # decay whatever the last hit left behind
        if flux > self.flux_avg * 2.2 and flux > 1e-4:
            self.hit = 1.0

    def features(self, prefix: str) -> dict[str, float]:
        out = {f"{prefix}.{name}": self.env[name] for name in BANDS}
        out[f"{prefix}.rms"] = self.rms
        out[f"{prefix}.hit"] = self.hit
        return out


class AudioAnalyzer:
    """Opens the input device and exposes a dict of 0..1 modulation sources."""

    def __init__(self, device: int | str | None = None) -> None:
        self.device = device
        self.stream: sd.InputStream | None = None
        self.error: str | None = None
        # Negotiated at start(). The hardware pass-through is always stereo,
        # but a dev machine's built-in mic is usually mono.
        self.channels = AUDIO_CHANNELS

        freqs = np.fft.rfftfreq(FFT_SIZE, 1.0 / AUDIO_RATE)
        self.window = np.hanning(FFT_SIZE).astype(np.float32)
        self.left = ChannelState(freqs)
        self.right = ChannelState(freqs)

        # Plain dict assignment is atomic in CPython, so the render thread can
        # read this without a lock while the audio thread replaces it.
        self._features: dict[str, float] = self._zero_features()

    @staticmethod
    def _zero_features() -> dict[str, float]:
        keys = []
        for side in ("l", "r", "mix"):
            keys += [f"{side}.{b}" for b in BANDS]
            keys += [f"{side}.rms", f"{side}.hit"]
        return dict.fromkeys(keys, 0.0)

    @property
    def features(self) -> dict[str, float]:
        return self._features

    @staticmethod
    def source_names() -> list[str]:
        return list(AudioAnalyzer._zero_features().keys())

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ARG002
        self.left.process(indata[:, 0], self.window)
        f = self.left.features("l")
        if indata.shape[1] > 1:
            self.right.process(indata[:, 1], self.window)
            f.update(self.right.features("r"))
        else:
            # Mono input: mirror L onto R rather than reporting a dead channel,
            # so patches that reference r.* still respond on a dev machine.
            f.update({f"r.{k.split('.', 1)[1]}": v for k, v in f.items()})
        # "mix" is a convenience source for patches that do not care about
        # stereo; the underlying analysis stays independent.
        for name in list(BANDS) + ["rms", "hit"]:
            f[f"mix.{name}"] = max(f[f"l.{name}"], f[f"r.{name}"])
        self._features = f

    def start(self) -> bool:
        try:
            # Open at most what the device actually offers; asking a mono mic
            # for two channels is a hard PortAudio error.
            info = sd.query_devices(self.device, "input")
            self.channels = min(AUDIO_CHANNELS, int(info["max_input_channels"]))
            if self.channels < 1:
                raise ValueError("device has no input channels")

            self.stream = sd.InputStream(
                device=self.device,
                samplerate=AUDIO_RATE,
                blocksize=AUDIO_BLOCK,
                channels=self.channels,
                dtype="float32",
                callback=self._callback,
            )
            self.stream.start()
            return True
        except Exception as exc:  # no device, wrong channel count, denied perms
            self.error = str(exc)
            self.stream = None
            return False

    def stop(self) -> None:
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None

    def describe(self) -> str:
        if self.stream is None:
            return f"audio: OFF ({self.error or 'no device'})"
        info = sd.query_devices(self.stream.device, "input")
        mono = " (mono, R mirrored)" if self.channels == 1 else ""
        return f"audio: {info['name']} @ {AUDIO_RATE} Hz, {self.channels}ch{mono}"
