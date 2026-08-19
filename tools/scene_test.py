"""Scene recall and soft-takeover checks.

Both fail quietly rather than loudly: a scene that drops a routing still
recalls, and a takeover bug just makes a knob feel wrong. These assert the
behaviour directly instead of trusting it.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import mido

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vsynth.engine.patch import build_patch
from vsynth.engine.scenes import SceneBank
from vsynth.midi.router import MidiRouter

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}{'  -- ' + detail if detail else ''}")
    if not ok:
        failures.append(name)


def rig(tmp: Path):
    bank, effects, _mixer, _master = build_patch()
    return bank, effects, SceneBank(bank, effects, tmp / "scenes.json")


def cc(router, control: int, value: int, channel: int = 0) -> None:
    router._on_message(mido.Message("control_change", channel=channel,
                                    control=control, value=value))


def main() -> int:
    tmp = Path(tempfile.mkdtemp())

    print("store and recall")
    bank, effects, scenes = rig(tmp)
    bank.get("color.hue").base = 0.8
    bank.get("feedback.mix").base = 0.6
    bank.get("color.sat").mod_source = "clk.pulse"
    bank.get("color.sat").mod_depth = -0.4
    effects[0].enabled = False
    scenes.store(0)

    bank.get("color.hue").base = 0.1
    bank.get("color.sat").mod_source = None
    effects[0].enabled = True
    check("recall restores control positions",
          scenes.recall(0) and abs(bank.get("color.hue").base - 0.8) < 1e-6,
          f"hue={bank.get('color.hue').base:.2f}")
    check("recall restores modulation routing",
          bank.get("color.sat").mod_source == "clk.pulse"
          and abs(bank.get("color.sat").mod_depth + 0.4) < 1e-6)
    check("recall restores bypass state", not effects[0].enabled)

    print("\nslots")
    bank.get("color.hue").base = 0.2
    scenes.store(1)
    scenes.recall(0)
    check("slots hold different looks", abs(bank.get("color.hue").base - 0.8) < 1e-6)
    scenes.recall(1)
    check("second slot recalls its own look", abs(bank.get("color.hue").base - 0.2) < 1e-6)
    check("empty slot recalls nothing", scenes.recall(4) is False)
    check("out-of-range store refused", scenes.store(99) is False)
    check("filled slots reported", scenes.filled() == [0, 1], f"{scenes.filled()}")

    print("\nedited state")
    scenes.recall(0)
    check("clean right after recall", not scenes.dirty and scenes.led(0) == "on")
    scenes.touch()
    check("edit marks the scene", scenes.dirty and scenes.led(0) == "blink")
    check("other stored slot reads as stored", scenes.led(1) == "dim")
    check("empty slot reads as empty", scenes.led(5) == "off")
    bank.get("color.hue").base = 0.99
    check("revert discards the edit",
          scenes.revert() and abs(bank.get("color.hue").base - 0.8) < 1e-6
          and not scenes.dirty)

    print("\npersistence")
    scenes.store(3)
    bank2, effects2, scenes2 = rig(tmp)
    check("scenes load from disk", scenes2.load() and scenes2.filled() == [0, 1, 3],
          f"{scenes2.filled()}")
    scenes2.recall(0)
    check("values survive the round trip", abs(bank2.get("color.hue").base - 0.8) < 1e-6)
    check("routings survive the round trip",
          bank2.get("color.sat").mod_source == "clk.pulse")
    scenes2.clear(1)
    check("clear removes a slot", scenes2.filled() == [0, 3], f"{scenes2.filled()}")

    # A scene written by an older build must not break a newer one.
    stale = json.loads((tmp / "scenes.json").read_text())
    stale["0"]["params"]["ghost.param"] = {"base": 0.5, "mod_source": None, "mod_depth": 0.0}
    stale["0"]["bypass"].pop("glitch", None)
    (tmp / "scenes.json").write_text(json.dumps(stale))
    bank3, effects3, scenes3 = rig(tmp)
    scenes3.load()
    ok = scenes3.recall(0)
    glitch = next(e for e in effects3 if e.key == "glitch")
    check("unknown controls in a stored scene are ignored", ok)
    check("effects missing from a scene default to enabled", glitch.enabled)

    print("\nsoft takeover")
    bank4, effects4, scenes4 = rig(tmp)
    router = MidiRouter(bank4)
    router.bindings = {(0, 20): "color.hue"}
    bank4.get("color.hue").base = 0.5
    router.invalidate_pickup()

    cc(router, 20, 0)     # far below the stored value
    check("far control is ignored", abs(bank4.get("color.hue").base - 0.5) < 1e-6,
          f"hue={bank4.get('color.hue').base:.3f}")
    check("waiting control is reported", router.waiting_count() == 1)
    check("pickup direction points the right way",
          router.pickup_status("color.hue") == "raise",
          f"{router.pickup_status('color.hue')!r}")

    cc(router, 20, 127)   # crosses the stored value
    check("crossing takes control", abs(bank4.get("color.hue").base - 1.0) < 1e-6,
          f"hue={bank4.get('color.hue').base:.3f}")
    cc(router, 20, 40)
    check("stays in control once caught", abs(bank4.get("color.hue").base - 40 / 127) < 1e-6)
    check("nothing waiting once caught", router.waiting_count() == 0)

    # Landing near the value should catch without needing to cross it.
    bank4.get("color.hue").base = 0.5
    router.invalidate_pickup()
    cc(router, 20, 64)    # 0.504, within tolerance of 0.5
    check("landing on the value takes control",
          abs(bank4.get("color.hue").base - 64 / 127) < 1e-6,
          f"hue={bank4.get('color.hue').base:.3f}")

    # Recall must drop everything back out of pickup.
    scenes4.store(0)
    router.invalidate_pickup()
    cc(router, 20, 127)
    bank4.get("color.hue").base = 0.5
    scenes4.recall(0)
    router.invalidate_pickup()
    # Compare against the recalled value itself rather than the value that was
    # stored: snapshots round to four decimals so the JSON stays readable, and
    # the property under test is that the control is ignored, not that scene
    # storage is bit-exact.
    recalled = bank4.get("color.hue").base
    cc(router, 20, 120)
    check("recall drops controls out of pickup",
          bank4.get("color.hue").base == recalled,
          f"hue={bank4.get('color.hue').base:.4f} (recalled {recalled:.4f})")

    router.pickup = False
    cc(router, 20, 10)
    check("pickup off responds immediately",
          abs(bank4.get("color.hue").base - 10 / 127) < 1e-6)

    # A control just moved to teach a mapping is already in hand.
    router.pickup = True
    router.invalidate_pickup()
    router.arm_learn("color.gain")
    cc(router, 21, 100)
    cc(router, 21, 30)
    check("a just-learned control responds at once",
          abs(bank4.get("color.gain").base - 30 / 127) < 1e-6,
          f"gain={bank4.get('color.gain').base:.3f}")

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s): {', '.join(failures)}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
