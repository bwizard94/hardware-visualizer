"""Musical clock: external MIDI clock with an internal fallback.

MIDI clock arrives at 24 pulses per quarter note -- only 48 Hz at 120 BPM,
below the render rate. Advancing phase only on ticks therefore stutters, so
phase is *interpolated* between ticks against a smoothed tempo estimate and
merely corrected by each tick. That is the difference between motion that
rides the music and motion that visibly steps.

When no external clock is present the same phase free-runs from an internal
tempo, so clock-driven modulation works on a bench with nothing plugged in --
and matches the tap-tempo button the panel is specified to have.
"""

from __future__ import annotations

import statistics
import time
from collections import deque

PPQN = 24            # MIDI clock pulses per quarter note, fixed by the spec
BEATS_PER_BAR = 4    # assumed 4/4; MIDI clock carries no time signature
TICK_TIMEOUT = 0.5   # no ticks for this long means the external clock is gone
TAP_TIMEOUT = 2.0    # taps further apart than this start a new tempo


class MidiClock:
    def __init__(self, internal_bpm: float = 120.0) -> None:
        self.internal_bpm = internal_bpm

        # External clock state, written from the MIDI thread.
        self._pos_ticks = 0          # tick index of the most recent tick
        self._next_tick = 0          # index the next tick will carry
        self._last_tick_at = 0.0
        self._intervals: deque[float] = deque(maxlen=PPQN)
        self._seconds_per_tick = 0.0
        self._transport_running = False
        # Whether an external clock has ever spoken. Without this, "transport
        # stopped" and "nothing is plugged in" look identical, and the internal
        # free-run takes over the moment a DAW hits stop.
        self._ever_external = False

        # Internal fallback, and the phase held when the transport stops.
        self._internal_epoch = time.perf_counter()
        self._frozen_beats = 0.0

        self._taps: deque[float] = deque(maxlen=4)

    # --- external clock, called from the MIDI thread -----------------------

    def on_tick(self, now: float) -> None:
        if self._last_tick_at > 0.0:
            interval = now - self._last_tick_at
            # Reject nonsense intervals: a dropped USB packet or a paused
            # sender would otherwise poison the tempo estimate for a full beat.
            if 0.0005 < interval < 0.5:
                self._intervals.append(interval)
                # Median rather than mean -- one late tick should not move the
                # estimate, and MIDI jitter is spiky rather than gaussian.
                self._seconds_per_tick = statistics.median(self._intervals)
        self._pos_ticks = self._next_tick
        self._next_tick += 1
        self._last_tick_at = now
        self._transport_running = True
        self._ever_external = True

    def on_start(self, now: float) -> None:
        """Start means "play from the top": the next tick is the downbeat."""
        self._pos_ticks = 0
        self._next_tick = 0
        self._last_tick_at = 0.0
        self._intervals.clear()
        self._transport_running = True
        self._ever_external = True

    def on_continue(self, now: float) -> None:
        self._transport_running = True

    def on_stop(self, now: float) -> None:
        # Freeze where we are rather than snapping to zero, so a stopped
        # transport holds the picture instead of jumping it.
        self._frozen_beats = self.beats(now)
        self._transport_running = False

    def on_song_position(self, midi_beats: int) -> None:
        """Song position is counted in sixteenth notes, i.e. 6 ticks each."""
        self._pos_ticks = midi_beats * (PPQN // 4)
        self._next_tick = self._pos_ticks + 1
        self._last_tick_at = 0.0
        # Hold the new position immediately: a locate arriving while the
        # transport is stopped must move the picture, not wait for the next
        # tick to reveal where we jumped to.
        self._frozen_beats = self._pos_ticks / PPQN
        self._ever_external = True

    # --- state -------------------------------------------------------------

    def external(self, now: float) -> bool:
        """True while an external clock is actually delivering ticks."""
        return (
            self._transport_running
            and self._last_tick_at > 0.0
            and (now - self._last_tick_at) < TICK_TIMEOUT
            and self._seconds_per_tick > 0.0
        )

    def bpm(self, now: float) -> float:
        if self.external(now):
            return 60.0 / (self._seconds_per_tick * PPQN)
        return self.internal_bpm

    def source(self, now: float) -> str:
        if self.external(now):
            return "midi"
        if self._ever_external:
            return "midi (stalled)" if self._transport_running else "midi (stopped)"
        return "internal"

    def beats(self, now: float) -> float:
        """Position in quarter notes, interpolated between ticks."""
        if self.external(now):
            elapsed = now - self._last_tick_at
            # Cap the extrapolation: if the next tick is late, drifting past it
            # would run the phase ahead and then snap back when it arrives.
            frac = min(elapsed / self._seconds_per_tick, 1.0)
            return (self._pos_ticks + frac) / PPQN

        if self._ever_external:
            # An external clock is in charge but not currently ticking. Hold
            # position rather than drifting: a stopped transport should freeze
            # the picture, and a pulled cable should not send it spinning off
            # at some unrelated internal tempo. Tap or downbeat takes manual
            # control back if the sender is genuinely gone.
            return self._frozen_beats if not self._transport_running \
                else self._pos_ticks / PPQN

        return self._frozen_beats + (now - self._internal_epoch) * self.internal_bpm / 60.0

    # --- tap tempo ---------------------------------------------------------

    def tap(self, now: float) -> float | None:
        """Set the internal tempo from tap spacing. Returns the new BPM."""
        if self._taps and (now - self._taps[-1]) > TAP_TIMEOUT:
            self._taps.clear()
        self._taps.append(now)
        if len(self._taps) < 2:
            return None

        spans = [b - a for a, b in zip(self._taps, list(self._taps)[1:])]
        bpm = 60.0 / (sum(spans) / len(spans))
        if not (20.0 <= bpm <= 300.0):
            return None

        # Realign the internal phase so the beat lands on this tap, not
        # wherever the old epoch happened to put it.
        self.internal_bpm = bpm
        self._frozen_beats = round(self.beats(now))
        self._internal_epoch = now
        self._ever_external = False  # tapping takes over from an external clock
        return bpm

    def reset_phase(self, now: float) -> None:
        self._frozen_beats = 0.0
        self._internal_epoch = now
        self._ever_external = False

    # --- modulation sources ------------------------------------------------

    @staticmethod
    def source_names() -> list[str]:
        return ["clk.beat", "clk.bar", "clk.8th", "clk.16th", "clk.pulse", "clk.tri"]

    def features(self, now: float) -> dict[str, float]:
        """Rhythmic modulation sources, all 0..1 like the audio ones.

        Ramps for sweeps, a decaying pulse for accents, a triangle for motion
        that needs to come back rather than snap.
        """
        beats = self.beats(now)
        phase = beats % 1.0
        return {
            "clk.beat": phase,
            "clk.bar": (beats / BEATS_PER_BAR) % 1.0,
            "clk.8th": (beats * 2.0) % 1.0,
            "clk.16th": (beats * 4.0) % 1.0,
            # Cubed so the accent is a spike rather than a slope.
            "clk.pulse": (1.0 - phase) ** 3,
            "clk.tri": 1.0 - abs(phase * 2.0 - 1.0),
        }

    def shader_clock(self, now: float) -> tuple[float, float, float, float]:
        """(beat phase, bar phase, beat pulse, total beats) for u_clock."""
        beats = self.beats(now)
        phase = beats % 1.0
        return (phase, (beats / BEATS_PER_BAR) % 1.0, (1.0 - phase) ** 3, beats)
