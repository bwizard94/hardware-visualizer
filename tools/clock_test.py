"""Clock timing checks.

Phase interpolation and tempo estimation are the parts of clock sync that fail
quietly -- a wrong constant still produces motion, just motion that drifts off
the music. These drive MidiClock from a synthetic tick stream with controlled
timing so the numbers can actually be checked.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vsynth.midi.clock import PPQN, MidiClock

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}{'  -- ' + detail if detail else ''}")
    if not ok:
        failures.append(name)


def feed(clock: MidiClock, bpm: float, beats: float, t0: float = 1000.0,
         jitter: list[float] | None = None) -> float:
    """Push a tick stream at the given tempo. Returns the final timestamp."""
    spt = 60.0 / (bpm * PPQN)
    n = int(beats * PPQN)
    t = t0
    for i in range(n):
        offset = jitter[i % len(jitter)] if jitter else 0.0
        clock.on_tick(t + offset)
        t += spt
    return t - spt


def main() -> int:
    print("tempo estimation")
    for bpm in (90.0, 120.0, 174.0):
        c = MidiClock()
        c.on_start(1000.0)
        last = feed(c, bpm, 4.0)
        est = c.bpm(last)
        check(f"{bpm:g} BPM recovered", abs(est - bpm) < 0.5, f"got {est:.2f}")

    print("\nphase")
    c = MidiClock()
    c.on_start(1000.0)
    last = feed(c, 120.0, 4.0)  # 4 beats at 120 BPM = 2.0 s
    check("position after 4 beats", abs(c.beats(last) - 3.958) < 0.05,
          f"{c.beats(last):.3f} beats")

    # The whole point of interpolation: phase must advance between ticks, not
    # sit still and jump. Sample within one tick interval.
    spt = 60.0 / (120.0 * PPQN)
    samples = [c.beats(last + spt * f) for f in (0.0, 0.25, 0.5, 0.75)]
    rising = all(a < b for a, b in zip(samples, samples[1:]))
    spread = samples[-1] - samples[0]
    check("phase interpolates between ticks", rising and spread > 0.005,
          f"advanced {spread:.4f} beats within one tick")

    # And it must not run past the next tick, or it snaps back when one lands.
    over = c.beats(last + spt * 4.0) - c.beats(last)
    check("extrapolation is capped", over <= 1.0 / PPQN + 1e-6,
          f"capped at {over * PPQN:.2f} ticks")

    print("\njitter rejection")
    c = MidiClock()
    c.on_start(1000.0)
    # One badly late tick per beat: the median estimator should ignore it where
    # a mean would drag the tempo down.
    last = feed(c, 120.0, 4.0, jitter=[0.0] * 23 + [0.004])
    est = c.bpm(last)
    check("late ticks do not move tempo", abs(est - 120.0) < 2.0, f"got {est:.2f}")

    print("\ntransport")
    c = MidiClock()
    c.on_start(1000.0)
    last = feed(c, 120.0, 2.0)
    held = c.beats(last)
    c.on_stop(last)
    check("stop freezes phase", abs(c.beats(last + 5.0) - held) < 1e-6)

    c.on_start(2000.0)
    feed(c, 120.0, 1.0, t0=2000.0)
    check("start returns to the downbeat", c.beats(2000.0) < 0.05,
          f"{c.beats(2000.0):.4f} beats")

    c2 = MidiClock()
    c2.on_song_position(16)  # 16 sixteenths = 4 beats
    check("song position lands on the right beat", abs(c2.beats(0.0) - 4.0) < 1e-6,
          f"{c2.beats(0.0):.2f} beats")

    print("\nexternal vs internal")
    c = MidiClock(internal_bpm=100.0)
    check("internal when nothing is connected", c.source(1000.0) == "internal")
    check("internal tempo reported", abs(c.bpm(1000.0) - 100.0) < 1e-6)
    # Internal must actually advance, or clock modulation does nothing on a
    # bench with no MIDI attached.
    c.reset_phase(1000.0)
    check("internal phase advances", abs(c.beats(1000.6) - 1.0) < 0.01,
          f"{c.beats(1000.6):.3f} beats after 0.6 s at 100 BPM")

    c = MidiClock()
    c.on_start(1000.0)
    last = feed(c, 120.0, 2.0)
    check("external once ticking", c.source(last) == "midi")
    check("falls back when ticks stop", c.source(last + 2.0) != "midi",
          f"source became {c.source(last + 2.0)!r}")

    print("\ntap tempo")
    c = MidiClock()
    for i in range(4):
        bpm = c.tap(1000.0 + i * 0.5)  # 0.5 s apart = 120 BPM
    check("four taps at 0.5 s give 120 BPM", bpm is not None and abs(bpm - 120.0) < 1.0,
          f"got {bpm:.2f}" if bpm else "no tempo")
    check("tap lands on a downbeat", abs(c.beats(1001.5) % 1.0) < 0.01,
          f"phase {c.beats(1001.5) % 1.0:.4f}")

    print("\nmodulation sources")
    c = MidiClock(internal_bpm=120.0)
    c.reset_phase(1000.0)
    names = set(MidiClock.source_names())
    check("declared sources all produced", set(c.features(1000.0)) == names)
    for t, label in ((1000.0, "downbeat"), (1000.25, "quarter"), (1000.4, "late")):
        f = c.features(t)
        check(f"all sources in range at {label}",
              all(0.0 <= v <= 1.0 for v in f.values()),
              ", ".join(f"{k.split('.')[1]}={v:.2f}" for k, v in sorted(f.items())))
    # Pulse must spike on the beat and be gone by mid-beat, or accents smear.
    check("pulse spikes on the beat",
          c.features(1000.0)["clk.pulse"] > 0.95 and c.features(1000.25)["clk.pulse"] < 0.2,
          f"{c.features(1000.0)['clk.pulse']:.2f} -> {c.features(1000.25)['clk.pulse']:.2f}")

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s): {', '.join(failures)}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
